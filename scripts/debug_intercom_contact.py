"""One-off Intercom contact inspector.

Fetches a contact two ways:
  1. POST /contacts/search   (what our daily script uses)
  2. GET  /contacts/{id}     (the full contact object)

Pretty-prints both responses and flags any field path that contains the
target session count we're hunting for. Use this when the search response
is missing data the Intercom UI clearly has.

Run via workflow_dispatch with DEBUG_EMAIL env var set.
"""

import json
import os
import sys

import requests

INTERCOM_TOKEN = os.environ['INTERCOM_ACCESS_TOKEN']
EMAIL = os.environ.get('DEBUG_EMAIL', '').strip().lower()
EXPECTED = os.environ.get('EXPECTED_SESSION_COUNT', '48').strip()

if not EMAIL:
    print('Set DEBUG_EMAIL.')
    sys.exit(1)


HEADERS_BASE = {
    'Authorization': f'Bearer {INTERCOM_TOKEN}',
    'Content-Type': 'application/json',
    'Accept': 'application/json',
}

VERSIONS_TO_TRY = ['2.10', '2.11', '2.12', '2.13', 'Unstable']


def headers_for(version):
    h = dict(HEADERS_BASE)
    h['Intercom-Version'] = version
    return h


def search_by_email(email, version):
    body = {
        'query': {
            'operator': 'AND',
            'value': [{'field': 'email', 'operator': '=', 'value': email}],
        },
        'pagination': {'per_page': 5},
    }
    r = requests.post('https://api.intercom.io/contacts/search', headers=headers_for(version), json=body)
    return r


def get_by_id(contact_id, version):
    r = requests.get(f'https://api.intercom.io/contacts/{contact_id}', headers=headers_for(version))
    return r


def find_paths(obj, target, path='$'):
    """Yield every JSON path whose stringified value equals target."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from find_paths(v, target, f'{path}.{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from find_paths(v, target, f'{path}[{i}]')
    else:
        if str(obj) == target:
            yield path


def find_keys_matching(obj, needle, path='$'):
    """Yield (path, value) for every dict key containing `needle` (case-insensitive)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if needle.lower() in k.lower():
                yield (f'{path}.{k}', v)
            yield from find_keys_matching(v, needle, f'{path}.{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from find_keys_matching(v, needle, f'{path}[{i}]')


def main():
    print(f'Looking up: {EMAIL}\n')

    # Use v2.10 to find the contact ID first.
    print('== Resolving contact ID via v2.10 search ==')
    r = search_by_email(EMAIL, '2.10')
    if r.status_code >= 300:
        print(f'  search failed: {r.status_code} {r.text[:300]}')
        return
    matches = r.json().get('data', [])
    if not matches:
        print('  No contact found.')
        return
    contact_id = matches[0]['id']
    print(f'  Contact id: {contact_id}\n')

    per_version = {}
    for version in VERSIONS_TO_TRY:
        print(f'== Trying Intercom-Version {version} ==')
        r = get_by_id(contact_id, version)
        if r.status_code >= 300:
            print(f'  GET failed: {r.status_code} {r.text[:200]}\n')
            continue
        data = r.json()
        per_version[version] = data
        print(f'  {len(data)} top-level fields')
        # Spot-check obvious session-related keys at top level
        for k in ('session_count', 'web_session_count', 'sessions', 'statistics', 'web_sessions'):
            if k in data:
                print(f'  {k} = {json.dumps(data[k])}')
        print()

    print('== Field hunt across all versions ==')
    print(f'Searching all paths whose value == "{EXPECTED}":')
    found = False
    for version, data in per_version.items():
        for p in find_paths(data, EXPECTED):
            print(f'  [v{version}] {p} = {EXPECTED}')
            found = True
    if not found:
        print(f'  (no field with value {EXPECTED!r} in any version response)')

    print('\nKeys whose name contains "session":')
    for version, data in per_version.items():
        for path, val in find_keys_matching(data, 'session'):
            print(f'  [v{version}] {path} = {json.dumps(val)}')

    print('\nKeys whose name contains "stat" or "metric":')
    for version, data in per_version.items():
        for needle in ('stat', 'metric'):
            for path, val in find_keys_matching(data, needle):
                print(f'  [v{version}] {path} = {json.dumps(val)}')

    # Diff field names between versions to see if anything new appears.
    print('\n== Top-level fields per version ==')
    keys_by_version = {v: set(d.keys()) for v, d in per_version.items()}
    all_keys = set().union(*keys_by_version.values()) if keys_by_version else set()
    for v in VERSIONS_TO_TRY:
        if v not in keys_by_version:
            continue
        extras = keys_by_version[v] - keys_by_version.get('2.10', set())
        if extras:
            print(f'  v{v} adds: {sorted(extras)}')
    print()


if __name__ == '__main__':
    main()
