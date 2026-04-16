import os
import requests
from datetime import datetime, timezone, timedelta

# ── Slack channels ────────────────────────────────────────────────────────────
# Publishers
PUB_ANZ_CHANNEL = 'C090Z7R8516'   # #pubsuite-client-health-check-anz
PUB_UK_CHANNEL  = 'C09LCBRPJSK'   # #pubsuite-client-health-check-uk
# Advertisers / Agencies
ADV_AU_CHANNEL  = 'C0ATC9AHKN0'   # #advertiser-activity-au
ADV_UK_CHANNEL  = 'C0AU2VB9VNU'   # #advertiser-activity-uk

# ── Deal pipelines ────────────────────────────────────────────────────────────
PUBLISHER_PIPELINES = {
    '1287994873',                            # Collab Platform Packages Pipeline
    '1288049116',                            # Collab Platform Onboarding Pipeline
    '1288861176',                            # Collab Platform Engagement Pipeline
    '930919882',                             # Pub SaaS
    '930940349',                             # AmpPlus
    '1029225915',                            # SaaS CS Pipeline
}
ADVERTISER_PIPELINES = {
    '1292285397',                            # AU Agency Private Platform Engagement Pipeline
    '956610025',                             # Agency SaaS
    't_ecaa8c5142c3c35eb072b4cff1cdb2f8',   # Collab Platform - Advertiser Pipeline
    'default',                               # Media Pipeline
    '942157253',                             # Payment Schedules
}

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
        return {'name': None, 'country': None, 'market_office_location': None,
                'owner_id': contact_owner_id, 'publisher_size': None, 'client_type': None,
                'deal_pipelines': set()}
    company_id = resp.json()['results'][0]['id']
    resp = requests.get(
        f'https://api.hubapi.com/crm/v3/objects/companies/{company_id}',
        headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
        params={'properties': 'name,country,market_office_location,hubspot_owner_id,publisher_size,client_type'},
    )
    if resp.status_code != 200:
        return {'name': None, 'country': None, 'market_office_location': None,
                'owner_id': contact_owner_id, 'publisher_size': None, 'client_type': None,
                'deal_pipelines': set()}
    props = resp.json().get('properties', {})

    # Get deals associated with this company
    deal_pipelines = set()
    deals_resp = requests.get(
        f'https://api.hubapi.com/crm/v3/objects/companies/{company_id}/associations/deals',
        headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
    )
    if deals_resp.status_code == 200:
        deal_ids = [d['id'] for d in deals_resp.json().get('results', [])[:20]]
        for deal_id in deal_ids:
            d_resp = requests.get(
                f'https://api.hubapi.com/crm/v3/objects/deals/{deal_id}',
                headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
                params={'properties': 'pipeline'},
            )
            if d_resp.status_code == 200:
                pl = d_resp.json().get('properties', {}).get('pipeline')
                if pl:
                    deal_pipelines.add(pl)

    return {
        'name': props.get('name'),
        'country': props.get('country'),
        'market_office_location': props.get('market_office_location'),
        'owner_id': props.get('hubspot_owner_id') or contact_owner_id,
        'publisher_size': props.get('publisher_size'),
        'client_type': props.get('client_type'),
        'deal_pipelines': deal_pipelines,
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


def classify_type(company):
    if not company:
        return 'Unknown'

    # 1. Check deal pipelines (most reliable)
    pipelines = company.get('deal_pipelines', set())
    has_pub = bool(pipelines & PUBLISHER_PIPELINES)
    has_adv = bool(pipelines & ADVERTISER_PIPELINES)
    if has_pub and not has_adv:
        return 'Publisher'
    if has_adv and not has_pub:
        return 'Advertiser'
    if has_pub and has_adv:
        return 'Publisher'  # default to publisher if in both

    # 2. Fallback: publisher_size field
    if company.get('publisher_size'):
        return 'Publisher'

    # 3. Fallback: client_type (non-Direct = Advertiser)
    client_type = company.get('client_type')
    if client_type and client_type != 'Direct':
        return 'Advertiser'

    return 'Unknown'


def format_message(contacts, region_label, type_label):
    today = datetime.now(timezone.utc).strftime('%d %b %Y')
    lines = [f"*New {type_label} Logins - Last 24 Hours ({region_label})* | {today}", '-' * 44]
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

    pub_au, pub_uk = [], []
    adv_au, adv_uk = [], []
    unknown = []

    for c in contacts:
        email = c.get('email') or ''
        name  = c.get('name') or email or 'Unknown'
        print(f'  Processing: {name} ({email})')
        company = get_hubspot_company_for_email(email)
        region  = classify_region(company)
        ctype   = classify_type(company)
        print(f'    -> {ctype} / {region}')
        enriched = {
            'name': name,
            'email': email or 'No email',
            'company_name': (company or {}).get('name') or 'No company',
            'first_seen_str': fmt_ts(c.get('created_at')),
        }
        if ctype == 'Publisher' and region == 'AU':
            pub_au.append(enriched)
        elif ctype == 'Publisher' and region == 'UK':
            pub_uk.append(enriched)
        elif ctype == 'Advertiser' and region == 'AU':
            adv_au.append(enriched)
        elif ctype == 'Advertiser' and region == 'UK':
            adv_uk.append(enriched)
        else:
            unknown.append(enriched)

    print(f'\nPub AU: {len(pub_au)}  Pub UK: {len(pub_uk)}')
    print(f'Adv AU: {len(adv_au)}  Adv UK: {len(adv_uk)}')
    print(f'Unknown: {len(unknown)}')

    if unknown:
        print('\nUnknown type/region (not posted to Slack):')
        for c in unknown:
            print(f'  - {c["name"]} ({c["email"]}) | {c["company_name"]}')

    if pub_au:
        post_to_slack(PUB_ANZ_CHANNEL, format_message(pub_au, 'ANZ', 'Publisher'))
        print(f'Posted {len(pub_au)} Publisher ANZ contact(s)')
    if pub_uk:
        post_to_slack(PUB_UK_CHANNEL, format_message(pub_uk, 'UK', 'Publisher'))
        print(f'Posted {len(pub_uk)} Publisher UK contact(s)')
    if adv_au:
        post_to_slack(ADV_AU_CHANNEL, format_message(adv_au, 'AU', 'Advertiser'))
        print(f'Posted {len(adv_au)} Advertiser AU contact(s)')
    if adv_uk:
        post_to_slack(ADV_UK_CHANNEL, format_message(adv_uk, 'UK', 'Advertiser'))
        print(f'Posted {len(adv_uk)} Advertiser UK contact(s)')

    if not any([pub_au, pub_uk, adv_au, adv_uk]):
        print('No contacts today - nothing posted to Slack')


if __name__ == '__main__':
    main()
