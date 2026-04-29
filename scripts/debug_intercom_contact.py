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


HEADERS = {
    'Authorization': f'Bearer {INTERCOM_TOKEN}',
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'Intercom-Version': '2.10',
}


def search_by_email(email):
    body = {
        'query': {
            'operator': 'AND',
            'value': [{'field': 'email', 'operator': '=', 'value': email}],
        },
        'pagination': {'per_page': 5},
    }
    r = requests.post('https://api.intercom.io/contacts/search', headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json().get('data', [])


def get_by_id(contact_id):
    r = requests.get(f'https://api.intercom.io/contacts/{contact_id}', headers=HEADERS)
    r.raise_for_status()
    return r.json()


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

    print('== /contacts/search response ==')
    matches = search_by_email(EMAIL)
    if not matches:
        print('  No contact found.')
        return
    contact = matches[0]
    print(json.dumps(contact, indent=2, sort_keys=True))

    contact_id = contact.get('id')
    if contact_id:
        print(f'\n== /contacts/{contact_id} (GET) response ==')
        full = get_by_id(contact_id)
        print(json.dumps(full, indent=2, sort_keys=True))
    else:
        full = contact

    print('\n== Field hunt ==')
    print(f'Searching all paths whose value == "{EXPECTED}":')
    found = False
    for src_label, src in (('search', contact), ('GET', full)):
        for p in find_paths(src, EXPECTED):
            print(f'  [{src_label}] {p} = {EXPECTED}')
            found = True
    if not found:
        print(f'  (no field with value {EXPECTED!r} in either response)')

    print('\nKeys whose name contains "session":')
    for src_label, src in (('search', contact), ('GET', full)):
        for path, val in find_keys_matching(src, 'session'):
            print(f'  [{src_label}] {path} = {json.dumps(val)}')

    print('\nKeys whose name contains "stat" or "metric":')
    for src_label, src in (('search', contact), ('GET', full)):
        for needle in ('stat', 'metric'):
            for path, val in find_keys_matching(src, needle):
                print(f'  [{src_label}] {path} = {json.dumps(val)}')


if __name__ == '__main__':
    main()
