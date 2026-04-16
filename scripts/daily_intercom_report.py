import os
import requests
from datetime import datetime, timezone, timedelta

# ── Slack channels ────────────────────────────────────────────────────────────
PUB_AU_CHANNEL = 'C090Z7R8516'   # #mediaowner-login-activity-anz
PUB_UK_CHANNEL = 'C09LCBRPJSK'   # #mediaowner-login-activity-uk
ADV_AU_CHANNEL = 'C0ATC9AHKN0'   # #advertiser-activity-au
ADV_UK_CHANNEL = 'C0AU2VB9VNU'   # #advertiser-activity-uk

# ── HubSpot owner IDs for the UK team (fallback when country fields are empty) ─
UK_OWNER_IDS = {
    358889915,  # Ben Micic
    358889914,  # Tom Gunter
    358889930,  # Ashleigh Webb
    361092319,  # Georgia Faure
    358889938,  # Madeleine Spicer
}

# ── Owner-based fallback for region (AU-focused owners) ──────────────────────
AU_OWNER_IDS = {
    79378340,    # Jade Scales (AU Advertiser)
    358889920,   # Daniel Walsh (AU Advertiser)
    358889921,   # Daniel Briggs (AU Advertiser)
    358889919,   # Claire Hansen (AU Advertiser)
    358889939,   # Senna Spear (AU Advertiser)
    358889918,   # Chloe Patterson (AU Publisher)
    # Cindy is AU+UK — excluded (region-ambiguous).
}

# ── Owner-based fallback for type (only used when pipelines are empty) ───────
PUBLISHER_OWNER_IDS = {
    358889918,   # Chloe Patterson (AU Publisher)
    75429652,    # Cindy Alexandra (AU+UK Publisher)
}
ADVERTISER_OWNER_IDS = {
    79378340,    # Jade Scales
    358889920,   # Daniel Walsh
    358889921,   # Daniel Briggs
    358889919,   # Claire Hansen
    358889939,   # Senna Spear
}

# ── HubSpot deal pipeline IDs ────────────────────────────────────────────────
PUBLISHER_PIPELINES = {
    '930919882',   # Pub SaaS
    '930940349',   # AmpPlus
    '1029225915',  # SaaS CS Pipeline
    '1287994873',  # Collab Platform Packages Pipeline
    '1288049116',  # Collab Platform Onboarding Pipeline
    '1288861176',  # Collab Platform Engagement Pipeline
}
ADVERTISER_PIPELINES = {
    'default',                              # Media Pipeline
    '956610025',                            # Agency SaaS
    '942157253',                            # Payment Schedules
    '1292285397',                           # AU Agency Private Platform Engagement Pipeline
    't_ecaa8c5142c3c35eb072b4cff1cdb2f8',   # AU Collab Platform - Advertiser Pipeline
}

# ── Country value sets (lowercase) ───────────────────────────────────────────
AU_VALUES = {'australia', 'au', 'anz', 'aus'}
UK_VALUES = {'united kingdom', 'uk', 'gb', 'great britain', 'england', 'scotland', 'wales'}

# ── API credentials + region filter ──────────────────────────────────────────
INTERCOM_TOKEN = os.environ['INTERCOM_ACCESS_TOKEN']
HUBSPOT_TOKEN  = os.environ['HUBSPOT_ACCESS_TOKEN']
SLACK_TOKEN    = os.environ['SLACK_BOT_TOKEN']
TARGET_REGION  = os.environ.get('REGION', 'ALL').upper()  # 'AU', 'UK', or 'ALL'


# ─────────────────────────────────────────────────────────────────────────────
# Intercom
# ─────────────────────────────────────────────────────────────────────────────

def get_new_intercom_contacts():
    """Return contacts whose created_at (first seen) is within the last 24 hours."""
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


# ─────────────────────────────────────────────────────────────────────────────
# HubSpot
# ─────────────────────────────────────────────────────────────────────────────

def _hs_headers():
    return {'Authorization': f'Bearer {HUBSPOT_TOKEN}', 'Content-Type': 'application/json'}


def get_hubspot_company_for_email(email):
    """
    Look up email in HubSpot contacts, find their associated company,
    and return a dict with: name, country, market_office_location, owner_id,
    publisher_size, client_type, deal_pipelines.
    Returns None if the contact doesn't exist in HubSpot.
    """
    if not email:
        return None

    # 1. Find contact by email
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
        print(f'  HubSpot contact search failed for {email}: {resp.status_code}')
        return None

    results = resp.json().get('results', [])
    if not results:
        return None

    contact = results[0]
    contact_id = contact['id']
    contact_owner_id = contact.get('properties', {}).get('hubspot_owner_id')

    # 2. Get associated companies
    resp = requests.get(
        f'https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}/associations/companies',
        headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
    )
    empty = {
        'name': None, 'country': None, 'market_office_location': None,
        'owner_id': contact_owner_id, 'publisher_size': None, 'client_type': None,
        'deal_pipelines': set(),
    }
    if resp.status_code != 200 or not resp.json().get('results'):
        return empty

    company_id = resp.json()['results'][0]['id']

    # 3. Fetch company properties
    resp = requests.get(
        f'https://api.hubapi.com/crm/v3/objects/companies/{company_id}',
        headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
        params={'properties': 'name,country,market_office_location,hubspot_owner_id,publisher_size,client_type'},
    )
    if resp.status_code != 200:
        return empty

    props = resp.json().get('properties', {})

    # 4. Fetch deal pipelines for associated deals
    pipelines = set()
    resp = requests.get(
        f'https://api.hubapi.com/crm/v3/objects/companies/{company_id}/associations/deals',
        headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
    )
    if resp.status_code == 200:
        for deal_ref in resp.json().get('results', []):
            deal_id = deal_ref['id']
            dresp = requests.get(
                f'https://api.hubapi.com/crm/v3/objects/deals/{deal_id}',
                headers={'Authorization': f'Bearer {HUBSPOT_TOKEN}'},
                params={'properties': 'pipeline'},
            )
            if dresp.status_code == 200:
                p = dresp.json().get('properties', {}).get('pipeline')
                if p:
                    pipelines.add(p)

    return {
        'name': props.get('name'),
        'country': props.get('country'),
        'market_office_location': props.get('market_office_location'),
        'owner_id': props.get('hubspot_owner_id') or contact_owner_id,
        'publisher_size': props.get('publisher_size'),
        'client_type': props.get('client_type'),
        'deal_pipelines': pipelines,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Region + type classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_region(company):
    """Return 'AU', 'UK', or 'Unknown'."""
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
            oid = int(owner_id)
            if oid in UK_OWNER_IDS:
                return 'UK'
            if oid in AU_OWNER_IDS:
                return 'AU'
        except (ValueError, TypeError):
            pass

    return 'Unknown'


def classify_type(company):
    """Return 'Publisher', 'Advertiser', or 'Unknown'."""
    if not company:
        return 'Unknown'

    pipelines = company.get('deal_pipelines') or set()
    has_pub = bool(pipelines & PUBLISHER_PIPELINES)
    has_adv = bool(pipelines & ADVERTISER_PIPELINES)
    if has_pub and not has_adv:
        return 'Publisher'
    if has_adv and not has_pub:
        return 'Advertiser'
    if has_pub and has_adv:
        return 'Publisher'  # tie-break: publisher wins

    if company.get('publisher_size'):
        return 'Publisher'

    client_type = company.get('client_type')
    if client_type and client_type != 'Direct':
        return 'Advertiser'

    # Owner-based fallback (only kicks in when pipeline + publisher_size + client_type all gave nothing)
    owner_id = company.get('owner_id')
    if owner_id:
        try:
            oid = int(owner_id)
            if oid in PUBLISHER_OWNER_IDS:
                return 'Publisher'
            if oid in ADVERTISER_OWNER_IDS:
                return 'Advertiser'
        except (ValueError, TypeError):
            pass

    return 'Unknown'


# ─────────────────────────────────────────────────────────────────────────────
# Slack
# ─────────────────────────────────────────────────────────────────────────────

def format_message(contacts, region_label, type_label, flag):
    today = datetime.now(timezone.utc).strftime('%d %b %Y')
    lines = [
        f"{flag} *New {type_label} Logins \u2013 Last 24 Hours ({region_label})* \u2502 {today}",
        '\u2500' * 44,
    ]
    for c in contacts:
        lines.append(f"\u2022 *{c['name']}* \u2502 {c['email']}")
        lines.append(f"  Company: {c['company_name']} \u2502 First seen: {c['first_seen_str']}")
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


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%d %b %H:%Mz')
    except Exception:
        return str(ts)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f'Region filter: {TARGET_REGION}')
    print('Fetching new Intercom contacts (last 24 hours)...')
    contacts = get_new_intercom_contacts()
    print(f'Found {len(contacts)} new contact(s)')

    pub_au, pub_uk, adv_au, adv_uk, unknown = [], [], [], [], []

    for c in contacts:
        email = c.get('email') or ''
        name  = c.get('name') or email or 'Unknown'
        print(f'  Processing: {name} ({email})')

        company = get_hubspot_company_for_email(email)
        region  = classify_region(company)
        ctype   = classify_type(company)

        enriched = {
            'name':         name,
            'email':        email or 'No email',
            'company_name': (company or {}).get('name') or 'No company',
            'first_seen_str': fmt_ts(c.get('created_at')),
        }

        if region == 'AU' and ctype == 'Publisher':
            pub_au.append(enriched)
        elif region == 'UK' and ctype == 'Publisher':
            pub_uk.append(enriched)
        elif region == 'AU' and ctype == 'Advertiser':
            adv_au.append(enriched)
        elif region == 'UK' and ctype == 'Advertiser':
            adv_uk.append(enriched)
        else:
            unknown.append({**enriched, 'region': region, 'type': ctype})

    print(f'\nPub AU: {len(pub_au)}  Pub UK: {len(pub_uk)}  Adv AU: {len(adv_au)}  Adv UK: {len(adv_uk)}  Unknown: {len(unknown)}')

    if unknown:
        print('\nUnknown (not posted to Slack):')
        for c in unknown:
            print(f'  - {c["name"]} ({c["email"]}) | {c["company_name"]} | region={c["region"]} type={c["type"]}')

    jobs = [
        (pub_au, PUB_AU_CHANNEL, 'ANZ', 'Publisher',  '\U0001f1e6\U0001f1fa', 'AU'),
        (pub_uk, PUB_UK_CHANNEL, 'UK',  'Publisher',  '\U0001f1ec\U0001f1e7', 'UK'),
        (adv_au, ADV_AU_CHANNEL, 'ANZ', 'Advertiser', '\U0001f1e6\U0001f1fa', 'AU'),
        (adv_uk, ADV_UK_CHANNEL, 'UK',  'Advertiser', '\U0001f1ec\U0001f1e7', 'UK'),
    ]
    for bucket, channel, region_label, type_label, flag, region_code in jobs:
        if TARGET_REGION != 'ALL' and TARGET_REGION != region_code:
            print(f'Skipping {type_label} {region_label} (region filter = {TARGET_REGION})')
            continue
        if bucket:
            post_to_slack(channel, format_message(bucket, region_label, type_label, flag))
            print(f'Posted {len(bucket)} {type_label} {region_label} contact(s) to Slack')
        else:
            print(f'No {type_label} {region_label} contacts — skipping')


if __name__ == '__main__':
    main()
