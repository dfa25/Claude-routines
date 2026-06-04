"""One-off HubSpot company inspector.

Searches HubSpot companies by name (CONTAINS_TOKEN) and dumps every match
with the properties the daily classifier uses for region/type. Also looks
up a domain to show what the email-domain fallback returns.

Run via the debug workflow with HS_QUERY_NAME and HS_QUERY_DOMAIN env vars.
"""

import json
import os
import sys

import requests

HUBSPOT_TOKEN = os.environ['HUBSPOT_ACCESS_TOKEN']
NAME_QUERY   = os.environ.get('HS_QUERY_NAME', '').strip()
DOMAIN_QUERY = os.environ.get('HS_QUERY_DOMAIN', '').strip().lower()

if not NAME_QUERY and not DOMAIN_QUERY:
    print('Set HS_QUERY_NAME and/or HS_QUERY_DOMAIN.')
    sys.exit(1)

HEADERS = {'Authorization': f'Bearer {HUBSPOT_TOKEN}',
           'Content-Type': 'application/json'}
PROPS = ['name', 'country', 'market_office_location', 'hubspot_owner_id',
         'domain', 'publisher_size', 'client_type', 'lifecyclestage']


def search_by_name(name):
    body = {
        'filterGroups': [{'filters': [
            {'propertyName': 'name', 'operator': 'CONTAINS_TOKEN', 'value': name}
        ]}],
        'properties': PROPS,
        'limit': 100,
    }
    r = requests.post('https://api.hubapi.com/crm/v3/objects/companies/search',
                      headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json().get('results', [])


def search_by_domain(domain):
    body = {
        'filterGroups': [{'filters': [
            {'propertyName': 'domain', 'operator': 'EQ', 'value': domain}
        ]}],
        'properties': PROPS,
        'limit': 50,
    }
    r = requests.post('https://api.hubapi.com/crm/v3/objects/companies/search',
                      headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json().get('results', [])


def dump(label, results):
    print(f'== {label} ({len(results)} match{"" if len(results)==1 else "es"}) ==')
    for c in results:
        props = c.get('properties', {})
        print(f"  id={c['id']}  name={props.get('name')!r}")
        print(f"      country: {props.get('country')!r}  "
              f"market_office: {props.get('market_office_location')!r}")
        print(f"      domain:  {props.get('domain')!r}")
        print(f"      owner:   {props.get('hubspot_owner_id')!r}")
        print(f"      publisher_size: {props.get('publisher_size')!r}  "
              f"client_type: {props.get('client_type')!r}")
        print(f"      lifecyclestage: {props.get('lifecyclestage')!r}")
        print()


def main():
    if NAME_QUERY:
        print(f'Searching companies by name CONTAINS_TOKEN {NAME_QUERY!r}...\n')
        dump(f'Name CONTAINS_TOKEN {NAME_QUERY!r}', search_by_name(NAME_QUERY))

    if DOMAIN_QUERY:
        print(f'Searching companies by domain == {DOMAIN_QUERY!r}...\n')
        dump(f'Domain == {DOMAIN_QUERY!r}', search_by_domain(DOMAIN_QUERY))


if __name__ == '__main__':
    main()
