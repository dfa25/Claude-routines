"""Audit global-agency company records in HubSpot.

For every agency domain in scripts/classification_overrides.json (the
type=Advertiser domains), this reports:

  1. What our email-domain fallback returns: the company HubSpot matches on
     `domain == <agency-domain>` exactly (this is the record a contact with no
     explicit company association lands on). Shows its country/office.
  2. All company records whose name contains the agency brand token, with each
     record's country/office/domain/owner — so we can see whether AU office
     records exist and whether they (a) carry the agency domain and (b) have
     country/office populated.

Read-only. Surfaces the mis-link pattern (AU records exist but the domain
points at the UK/global record, and AU records often have blank country).
"""

import json
import os
import re
from pathlib import Path

import requests

HUBSPOT_TOKEN = os.environ['HUBSPOT_ACCESS_TOKEN']
HEADERS = {'Authorization': f'Bearer {HUBSPOT_TOKEN}', 'Content-Type': 'application/json'}
PROPS = ['name', 'country', 'market_office_location', 'hubspot_owner_id', 'domain']

OVERRIDES = json.loads((Path(__file__).resolve().parent / 'classification_overrides.json').read_text())
AGENCY_DOMAINS = [d for d, v in OVERRIDES.get('domains', {}).items()
                  if v.get('type') == 'Advertiser']


def search_by_domain(domain):
    body = {'filterGroups': [{'filters': [{'propertyName': 'domain', 'operator': 'EQ', 'value': domain}]}],
            'properties': PROPS, 'limit': 50}
    r = requests.post('https://api.hubapi.com/crm/v3/objects/companies/search', headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json().get('results', [])


def search_by_name(token):
    body = {'filterGroups': [{'filters': [{'propertyName': 'name', 'operator': 'CONTAINS_TOKEN', 'value': token}]}],
            'properties': PROPS, 'limit': 100}
    r = requests.post('https://api.hubapi.com/crm/v3/objects/companies/search', headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json().get('results', [])


def brand_token(domain):
    # 'mindshareworld.com' -> 'mindshare' is hard; use the second-level label.
    label = domain.split('.')[0]
    # crude singularisation of common agency labels
    return label


AU_VALUES = {'australia', 'au', 'anz', 'aus'}
UK_VALUES = {'united kingdom', 'uk', 'gb', 'great britain', 'england', 'scotland', 'wales'}


def region_of(props):
    c = (props.get('country') or '').lower().strip()
    m = (props.get('market_office_location') or '').lower().strip()
    if c in AU_VALUES or any(v in m for v in AU_VALUES):
        return 'AU'
    if c in UK_VALUES or any(v in m for v in UK_VALUES):
        return 'UK'
    return '?'


def main():
    print(f'Auditing {len(AGENCY_DOMAINS)} agency domains\n')
    for domain in AGENCY_DOMAINS:
        print('=' * 64)
        print(f'AGENCY DOMAIN: {domain}')
        # 1. what the fallback resolves to
        dmatch = search_by_domain(domain)
        if dmatch:
            for c in dmatch:
                p = c['properties']
                print(f"  domain-fallback -> id={c['id']} name={p.get('name')!r} "
                      f"region={region_of(p)} country={p.get('country')!r} office={p.get('market_office_location')!r}")
        else:
            print('  domain-fallback -> (no company carries this exact domain)')

        # 2. brand-name records, summarised by region
        token = brand_token(domain)
        named = search_by_name(token)
        # only keep records that look like the agency itself (name starts with brand or 'Brand-XX'),
        # skip per-client deal records like 'Mars @ Zenith'
        agency_records = [c for c in named
                          if not re.search(r'@', c['properties'].get('name') or '')]
        au = [c for c in agency_records if region_of(c['properties']) == 'AU']
        uk = [c for c in agency_records if region_of(c['properties']) == 'UK']
        unk = [c for c in agency_records if region_of(c['properties']) == '?']
        print(f"  brand '{token}' agency records: {len(agency_records)}  (AU={len(au)} UK={len(uk)} unknown-region={len(unk)})")
        for c in agency_records:
            p = c['properties']
            print(f"     id={c['id']:>14} {str(p.get('name'))[:34]:34s} region={region_of(p):2s} "
                  f"domain={p.get('domain')!r} owner={p.get('hubspot_owner_id')!r}")
        print()


if __name__ == '__main__':
    main()
