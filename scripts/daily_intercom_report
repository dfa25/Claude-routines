import os
import requests
from datetime import datetime, timezone, timedelta

# ── Slack channels ────────────────────────────────────────────────────────────
ANZ_CHANNEL = 'C090Z7R8516'  # #pubsuite-client-health-check-anz
UK_CHANNEL  = 'C09LCBRPJSK'  # #pubsuite-client-health-check-uk

# ── HubSpot owner IDs for the UK team (fallback when country fields are empty) ─
UK_OWNER_IDS = {
    358889915,  # Ben Micic
    358889914,  # Tom Gunter
    358889930,  # Ashleigh Webb
    361092319,  # Georgia Faure
    358889938,  # Madeleine Spicer
}

# ── Country value sets (lowercase) ───────────────────────────────────────────
AU_VALUES = {'australia', 'au', 'anz', 'aus'}
UK_VALUES = {'united kingdom', 'uk', 'gb', 'great britain', 'england', 'scotland', 'wales'}

# ── API credentials ───────────────────────────────────────────────────────────
INTERCOM_TOKEN = os.environ['INTERCOM_ACCESS_TOKEN']
HUBSPOT_TOKEN  = os.environ['HUBSPOT_ACCESS_TOKEN']
SLACK_TOKEN    = os.environ['SLACK_BOT_TOKEN']


def get_new_intercom_contacts():
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())
    url = 'https://api.intercom.io/contacts/search'
    headers = {
        'Authorization': f'Bearer {INTERCOM_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Intercom-Version': '2.10',
    }
    body = {
        'query': {
            'operator': 'AND',
            'value': [{'field': 'created_at', 'operator': '>', 'value': cutoff}],
        },
        'pagination': {'per_page': 150},
    }
    contacts = []
    while True:
        resp = requests.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        contacts.extend(data.get('data', []))
        next_cursor = data.get('pages', {}).get('next', {}).get('starting_after')
        if not next_cursor:
            break
        body['pagination']['starting_after'] = next_cursor
    return contacts


def _hs_headers():
    return {'Authorization': f'Bearer {HUBSPOT_TOKEN}', 'Content-Type': 'application/json'}


def get_hubspot_company_for_email(email):
    if not email:
        return None
    resp = requests.post(
        'https://api.hubapi.com/crm/v3/objects/contacts/search',
        headers=_hs_headers(),
        json={
            'filterGroups': [{'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': email}]}],
            'properties': ['email', 'hubspot_owner_id'],
            'limit': 1,
        },
    )
    if resp.status_code != 200:
        return None
    results = resp.json().get('results', [])
    if not results:
        return None
    contact = results[0]
    contact_id = contact['id']
    contact_owner_id = contact.get('properties', {}).get('hubspot_owner_id')
    resp = requests.get(
        f'https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}/associations/companies',
        headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
    )
    if resp.status_code != 200 or not resp.json().get('results'):
        return {'name': None, 'country': None, 'market_office_location': None, 'owner_id': contact_owner_id}
    company_id = resp.json()['results'][0]['id']
    resp = requests.get(
        f'https://api.hubapi.com/crm/v3/objects/companies/{company_id}',
        headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
        params={'properties': 'name,country,market_office_location,hubspot_owner_id'},
    )
    if resp.status_code != 200:
        return {'name': None, 'country': None, 'market_office_location': None, 'owner_id': contact_owner_id}
    props = resp.json().get('properties', {})
    return {
        'name': props.get('name'),
        'country': props.get('country'),
        'market_office_location': props.get('market_office_location'),
        'owner_id': props.get('hubspot_owner_id') or contact_owner_id,
    }


def classify_region(company):
    if not company:
        return 'Unknown'
    country = (company.get('country') or '').lower().strip()
    if country in AU_VALUES:
        return 'AU'
    if country in UK_VALUES:
        return 'UK'
    market = (company.get('market_office_location') or '').lower().strip()
    if any(v in market for v in AU_VALUES):
        return 'AU'
    if any(v in market for v in UK_VALUES):
        return 'UK'
    owner_id = company.get('owner_id')
    if owner_id:
        try:
            if int(owner_id) in UK_OWNER_IDS:
                return 'UK'
        except (ValueError, TypeError):
            pass
    return 'Unknown'


def format_message(contacts, region_label, flag):
    today = datetime.now(timezone.utc).strftime('%d %b %Y')
    lines = [f"{flag} *New Logins - Last 24 Hours ({region_label})* | {today}", '-' * 44]
    for c in contacts:
        lines.append(f"- *{c['name']}* | {c['email']}")
        lines.append(f"  Company: {c['company_name']} | First seen: {c['first_seen_str']}")
    lines += ['', f"Total: {len(contacts)} new user{'s' if len(contacts) != 1 else ''}"]
    return '\n'.join(lines)


def post_to_slack(channel_id, text):
    resp = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': channel_id, 'text': text, 'mrkdwn': True},
    )
    data = resp.json()
    if not data.get('ok'):
        raise RuntimeError(f'Slack post failed: {data.get("error")}')


def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%d %b %H:%Mz')
    except Exception:
        return str(ts)


def main():
    print('Fetching new Intercom contacts (last 24 hours)...')
    contacts = get_new_intercom_contacts()
    print(f'Found {len(contacts)} new contact(s)')
    au, uk, unknown = [], [], []
    for c in contacts:
        email = c.get('email') or ''
        name  = c.get('name') or email or 'Unknown'
        print(f'  Processing: {name} ({email})')
        company = get_hubspot_company_for_email(email)
        region  = classify_region(company)
        enriched = {
            'name': name,
            'email': email or 'No email',
            'company_name': (company or {}).get('name') or 'No company',
            'first_seen_str': fmt_ts(c.get('created_at')),
        }
        if region == 'AU':
            au.append(enriched)
        elif region == 'UK':
            uk.append(enriched)
        else:
            unknown.append(enriched)
    print(f'\nAU: {len(au)}  UK: {len(uk)}  Unknown: {len(unknown)}')
    if unknown:
        print('\nUnknown region (not posted to Slack):')
        for c in unknown:
            print(f'  - {c["name"]} ({c["email"]}) | {c["company_name"]}')
    if au:
        post_to_slack(ANZ_CHANNEL, format_message(au, 'ANZ', 'AU'))
        print(f'Posted {len(au)} ANZ contact(s) to Slack')
    else:
        print('No AU contacts today - skipping ANZ Slack post')
    if uk:
        post_to_slack(UK_CHANNEL, format_message(uk, 'UK', 'UK'))
        print(f'Posted {len(uk)} UK contact(s) to Slack')
    else:
        print('No UK contacts today - skipping UK Slack post')


if __name__ == '__main__':
    main()
