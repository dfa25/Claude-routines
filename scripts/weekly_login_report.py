"""Weekly login report.

Reads the last 7 daily Intercom snapshots, builds per-user metrics for each
of the four buckets (Publisher/Advertiser x ANZ/UK), upserts rows into two
Notion databases (one for Publishers, one for Advertisers/Agency), and posts
a summary to the matching Slack channel.

Runs in two modes controlled by REGION env var: AU or UK. Each invocation
only processes that region's two buckets.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / 'data' / 'snapshots'

# ── Slack channels (mirror daily_intercom_report.py) ─────────────────────────
PUB_AU_CHANNEL = 'C090Z7R8516'
PUB_UK_CHANNEL = 'C09LCBRPJSK'
ADV_AU_CHANNEL = 'C0ATC9AHKN0'
ADV_UK_CHANNEL = 'C0AU2VB9VNU'

# ── Notion databases (in the "user data" workspace) ──────────────────────────
NOTION_DB_PUBLISHER = '34a789ce423180c19404f458b5d566c5'  # publisher logins
NOTION_DB_AGENCY    = '34a789ce423180b0b670c0971db144df'  # agency (advertiser) logins

NOTION_API = 'https://api.notion.com/v1'
NOTION_VERSION = '2022-06-28'

HUBSPOT_TOKEN = os.environ['HUBSPOT_ACCESS_TOKEN']
SLACK_TOKEN   = os.environ['SLACK_BOT_TOKEN']
NOTION_TOKEN  = os.environ['NOTION_TOKEN']
TARGET_REGION = os.environ.get('REGION', 'ALL').upper()  # 'AU', 'UK', or 'ALL'


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot loading
# ─────────────────────────────────────────────────────────────────────────────

def load_snapshots():
    """Return {date_str: [rows]} for every snapshot file we can parse."""
    out = {}
    if not SNAPSHOT_DIR.exists():
        return out
    for p in sorted(SNAPSHOT_DIR.glob('*.json')):
        try:
            out[p.stem] = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            print(f'  skipping {p.name}: {e}')
    return out


def window_dates(reference, days=7):
    """Return the list of YYYY-MM-DD strings for the 7 days ending on reference (inclusive)."""
    return [(reference - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days - 1, -1, -1)]


# ─────────────────────────────────────────────────────────────────────────────
# Per-user aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _ts_to_date(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except (ValueError, TypeError):
        return None


def aggregate_users(snapshots, window, team, region):
    """Build per-user metrics for users in the given bucket active in the window.

    - snapshots: {date_str: [rows]} (all dates we have)
    - window:    list of date strings inside the 7-day window
    - team:      'Publisher' or 'Advertiser'
    - region:    'AU' or 'UK'
    """
    window_start = window[0]
    window_start_ts = datetime.strptime(window_start, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()

    per_user = {}  # email -> aggregated dict

    # Walk window snapshots.
    for date_str in window:
        for row in snapshots.get(date_str, []):
            if row.get('region') != region or row.get('type') != team:
                continue
            email = row.get('email')
            if not email:
                continue

            u = per_user.setdefault(email, {
                'email':           email,
                'name':            row.get('name') or email,
                'company_name':    row.get('company_name') or 'No company',
                'company_id':      row.get('company_id'),
                'region':          region,
                'team':            team,
                'created_at':      row.get('created_at'),
                'active_dates':    set(),
                'last_seen_at':    0,
            })
            # Most recent row wins for descriptive fields.
            u['name'] = row.get('name') or u['name']
            u['company_name'] = row.get('company_name') or u['company_name']
            u['company_id'] = row.get('company_id') or u['company_id']
            u['created_at'] = row.get('created_at') or u['created_at']

            u['active_dates'].add(date_str)
            last_seen = row.get('last_seen_at') or 0
            if last_seen > u['last_seen_at']:
                u['last_seen_at'] = last_seen

    # Finalise: Logins (7d) = count of distinct days user appeared in snapshots
    # (last_seen_at-based signal, since Intercom's session_count isn't populated).
    for email, u in per_user.items():
        u['logins_7d'] = len(u['active_dates'])

        created_at = u.get('created_at') or 0
        u['user_type'] = 'New' if created_at and created_at >= window_start_ts else 'Returning'

    return list(per_user.values())


# ─────────────────────────────────────────────────────────────────────────────
# HubSpot: total users per org (penetration denominator)
# ─────────────────────────────────────────────────────────────────────────────

_hs_contact_cache = {}

def hubspot_contact_count(company_id):
    """Return number of contacts associated with a HubSpot company, or None."""
    if not company_id:
        return None
    if company_id in _hs_contact_cache:
        return _hs_contact_cache[company_id]

    total = 0
    after = None
    while True:
        params = {'limit': 100}
        if after:
            params['after'] = after
        resp = requests.get(
            f'https://api.hubapi.com/crm/v3/objects/companies/{company_id}/associations/contacts',
            headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
            params=params,
        )
        if resp.status_code != 200:
            _hs_contact_cache[company_id] = None
            return None
        data = resp.json()
        total += len(data.get('results', []))
        paging = data.get('paging', {}).get('next')
        if not paging:
            break
        after = paging.get('after')
    _hs_contact_cache[company_id] = total
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Org rollup
# ─────────────────────────────────────────────────────────────────────────────

def rollup_by_org(users):
    """Group users by organisation and compute org-level metrics."""
    orgs = defaultdict(lambda: {
        'name': None, 'company_id': None, 'users': [],
        'active_users': 0, 'new_users': 0, 'returning_users': 0,
        'logins_7d': 0, 'new_user_logins_7d': 0, 'returning_user_logins_7d': 0,
        'total_users_hubspot': None,
    })
    for u in users:
        key = u['company_name']
        o = orgs[key]
        o['name'] = key
        if u.get('company_id'):
            o['company_id'] = u['company_id']
        o['users'].append(u)
        o['active_users'] += 1
        o['logins_7d'] += u['logins_7d']
        if u['user_type'] == 'New':
            o['new_users'] += 1
            o['new_user_logins_7d'] += u['logins_7d']
        else:
            o['returning_users'] += 1
            o['returning_user_logins_7d'] += u['logins_7d']

    for o in orgs.values():
        o['total_users_hubspot'] = hubspot_contact_count(o['company_id'])
        tu = o['total_users_hubspot']
        o['penetration_pct'] = (o['active_users'] / tu) if tu else None

    return orgs


# ─────────────────────────────────────────────────────────────────────────────
# Notion
# ─────────────────────────────────────────────────────────────────────────────

def _notion_headers():
    return {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Notion-Version': NOTION_VERSION,
        'Content-Type': 'application/json',
    }


DESIRED_SCHEMA = {
    # Notion DBs always auto-create a Title column; we name it "User" via update.
    'Email':                       {'email': {}},
    'Organisation':                {'rich_text': {}},
    'Region':                      {'select': {'options': [
        {'name': 'ANZ', 'color': 'blue'},
        {'name': 'UK',  'color': 'purple'},
    ]}},
    'Week of':                     {'date': {}},
    'Last login':                  {'date': {}},
    'Logins (7d)':                 {'number': {'format': 'number'}},
    'User type':                   {'select': {'options': [
        {'name': 'New',       'color': 'green'},
        {'name': 'Returning', 'color': 'gray'},
    ]}},
    'Active users at org':         {'number': {'format': 'number'}},
    'Total users at org':          {'number': {'format': 'number'}},
    'Org penetration %':           {'number': {'format': 'percent'}},
    'Org logins (7d)':             {'number': {'format': 'number'}},
    'New user logins (7d)':        {'number': {'format': 'number'}},
    'Returning user logins (7d)':  {'number': {'format': 'number'}},
}


def ensure_database_schema(database_id):
    """Idempotently ensure all DESIRED_SCHEMA properties exist on the Notion DB.
    Also renames the title property to "User" if it isn't already."""
    resp = requests.get(f'{NOTION_API}/databases/{database_id}', headers=_notion_headers())
    resp.raise_for_status()
    existing = resp.json().get('properties', {})

    patch = {}

    # Rename title to "User" if needed.
    title_prop = next((name for name, p in existing.items() if p.get('type') == 'title'), None)
    if title_prop and title_prop != 'User':
        patch[title_prop] = {'name': 'User'}

    for prop_name, prop_def in DESIRED_SCHEMA.items():
        if prop_name not in existing:
            patch[prop_name] = prop_def

    if not patch:
        return

    resp = requests.patch(
        f'{NOTION_API}/databases/{database_id}',
        headers=_notion_headers(),
        json={'properties': patch},
    )
    if resp.status_code >= 300:
        raise RuntimeError(f'Notion schema patch failed: {resp.status_code} {resp.text}')
    print(f'  Notion schema updated for {database_id}: {list(patch.keys())}')


def _notion_find_existing(database_id, email, week_of):
    """Return Notion page id for (email, week_of) if it exists, else None."""
    body = {
        'filter': {
            'and': [
                {'property': 'Email',    'email':  {'equals': email}},
                {'property': 'Week of',  'date':   {'equals': week_of}},
            ],
        },
        'page_size': 1,
    }
    resp = requests.post(
        f'{NOTION_API}/databases/{database_id}/query',
        headers=_notion_headers(),
        json=body,
    )
    if resp.status_code >= 300:
        print(f'  Notion query failed: {resp.status_code} {resp.text[:300]}')
        return None
    results = resp.json().get('results', [])
    return results[0]['id'] if results else None


def _notion_properties(user, org, week_of, region_label):
    """Build a Notion properties payload for a user row."""
    last_login_iso = None
    if user.get('last_seen_at'):
        try:
            last_login_iso = datetime.fromtimestamp(int(user['last_seen_at']), tz=timezone.utc).date().isoformat()
        except (ValueError, TypeError):
            pass

    props = {
        'User':                        {'title':     [{'text': {'content': user['name'] or user['email']}}]},
        'Email':                       {'email':     user['email']},
        'Organisation':                {'rich_text': [{'text': {'content': user['company_name'] or 'No company'}}]},
        'Region':                      {'select':    {'name': region_label}},
        'Week of':                     {'date':      {'start': week_of}},
        'Logins (7d)':                 {'number':    user['logins_7d']},
        'User type':                   {'select':    {'name': user['user_type']}},
        'Active users at org':         {'number':    org['active_users']},
        'Org logins (7d)':             {'number':    org['logins_7d']},
        'New user logins (7d)':        {'number':    org['new_user_logins_7d']},
        'Returning user logins (7d)':  {'number':    org['returning_user_logins_7d']},
    }
    if last_login_iso:
        props['Last login'] = {'date': {'start': last_login_iso}}
    if org.get('total_users_hubspot') is not None:
        props['Total users at org'] = {'number': org['total_users_hubspot']}
    if org.get('penetration_pct') is not None:
        props['Org penetration %'] = {'number': org['penetration_pct']}
    return props


def upsert_user_rows(database_id, users, orgs, week_of, region_label):
    for u in users:
        org = orgs[u['company_name']]
        props = _notion_properties(u, org, week_of, region_label)
        existing_id = _notion_find_existing(database_id, u['email'], week_of)
        if existing_id:
            resp = requests.patch(
                f'{NOTION_API}/pages/{existing_id}',
                headers=_notion_headers(),
                json={'properties': props},
            )
        else:
            resp = requests.post(
                f'{NOTION_API}/pages',
                headers=_notion_headers(),
                json={'parent': {'database_id': database_id}, 'properties': props},
            )
        if resp.status_code >= 300:
            print(f'  Notion upsert failed for {u["email"]}: {resp.status_code} {resp.text[:300]}')


# ─────────────────────────────────────────────────────────────────────────────
# Slack
# ─────────────────────────────────────────────────────────────────────────────

def post_to_slack(channel_id, text):
    resp = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': channel_id, 'text': text, 'mrkdwn': True},
    )
    data = resp.json()
    if not data.get('ok'):
        raise RuntimeError(f'Slack post failed: {data.get("error")}')


def format_slack_summary(team_label, region_label, flag, window, users, orgs, notion_db_id):
    start = datetime.strptime(window[0], '%Y-%m-%d').strftime('%a %d %b')
    end   = datetime.strptime(window[-1], '%Y-%m-%d').strftime('%a %d %b')

    total_unique = len(users)
    new_users = sum(1 for u in users if u['user_type'] == 'New')
    ret_users = total_unique - new_users
    total_logins = sum(u['logins_7d'] for u in users)
    new_logins = sum(u['logins_7d'] for u in users if u['user_type'] == 'New')
    ret_logins = total_logins - new_logins
    active_orgs = len(orgs)

    lines = [
        f"{flag} *Weekly Login Recap — {team_label} {region_label}* │ {start} → {end}",
        '─' * 52,
        f"Unique users: *{total_unique}*  ({ret_users} returning · {new_users} new)",
        f"Total logins: *{total_logins}*  ({ret_logins} returning · {new_logins} new)",
        f"Active orgs: *{active_orgs}*",
        '_Logins = distinct days each user was active in the 7-day window._',
        '',
    ]

    for org in sorted(orgs.values(), key=lambda o: (-o['logins_7d'], o['name'] or '')):
        tu = org.get('total_users_hubspot')
        pen = org.get('penetration_pct')
        pen_str = f" ({pen * 100:.0f}%)" if pen is not None else ''
        ratio_str = f"{org['active_users']}/{tu}" if tu else f"{org['active_users']}"
        lines.append(
            f"• *{org['name']}* — {ratio_str} users{pen_str}, {org['logins_7d']} logins "
            f"({org['new_user_logins_7d']} new · {org['returning_user_logins_7d']} returning)"
        )
        for u in sorted(org['users'], key=lambda x: -x['logins_7d']):
            last_login_str = ''
            if u.get('last_seen_at'):
                try:
                    last_login_str = datetime.fromtimestamp(int(u['last_seen_at']), tz=timezone.utc).strftime('%a %d %b')
                except (ValueError, TypeError):
                    pass
            tag = '🆕' if u['user_type'] == 'New' else '↩️'
            lines.append(
                f"   {tag} {u['email']} · last {last_login_str} · "
                f"{u['logins_7d']} login{'s' if u['logins_7d'] != 1 else ''}"
            )
        lines.append('')

    lines.append(f"📄 Notion: https://www.notion.so/{notion_db_id.replace('-', '')}")
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if TARGET_REGION not in ('AU', 'UK', 'ALL'):
        print(f'Unknown REGION: {TARGET_REGION}')
        sys.exit(1)

    print(f'Region filter: {TARGET_REGION}')
    snapshots = load_snapshots()
    if not snapshots:
        print('No snapshots found. Nothing to report.')
        return

    # Window cadence: both regions run Mon 9am Sydney (Sun 23:00 UTC).
    # Window = the Mon → Sun that just ended (7 days ending on the run day in UTC).
    today = datetime.now(timezone.utc).date()
    window = window_dates(today, days=7)
    week_of = window[0]  # date representing the start of the window
    print(f'Window: {window[0]} → {window[-1]} (week_of={week_of})')

    jobs = [
        ('Publisher',  'AU', 'ANZ', '🇦🇺', PUB_AU_CHANNEL, NOTION_DB_PUBLISHER),
        ('Publisher',  'UK', 'UK',  '🇬🇧', PUB_UK_CHANNEL, NOTION_DB_PUBLISHER),
        ('Advertiser', 'AU', 'ANZ', '🇦🇺', ADV_AU_CHANNEL, NOTION_DB_AGENCY),
        ('Advertiser', 'UK', 'UK',  '🇬🇧', ADV_UK_CHANNEL, NOTION_DB_AGENCY),
    ]

    schemas_ensured = set()
    for team, region, region_label, flag, channel, db_id in jobs:
        if TARGET_REGION != 'ALL' and TARGET_REGION != region:
            continue
        print(f'\n=== {team} {region_label} ===')
        users = aggregate_users(snapshots, window, team, region)
        if not users:
            print(f'  No active users in window.')
            continue
        orgs = rollup_by_org(users)

        if db_id not in schemas_ensured:
            ensure_database_schema(db_id)
            schemas_ensured.add(db_id)

        upsert_user_rows(db_id, users, orgs, week_of, region_label)
        msg = format_slack_summary(team, region_label, flag, window, users, orgs, db_id)
        post_to_slack(channel, msg)
        print(f'  Posted weekly summary: {len(users)} users across {len(orgs)} orgs')


if __name__ == '__main__':
    main()
