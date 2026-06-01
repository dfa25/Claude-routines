"""One-off backfill: recover silently-dropped logins into the weekly Notion rollups.

Between 2026-05-15 and 2026-05-31 a classification bug stored many active
contacts as type=Unknown (HubSpot records lacked a type signal), so they were
posted to no channel and never made it into the weekly Notion databases.

The fix (commit 27d2e82) added domain/email overrides, a `.london` TLD, and a
type-Unknown -> Publisher fallback. This script replays that corrected
classification over the *already-committed* daily snapshots and upserts the
recomputed weekly rows into Notion.

Why no HubSpot re-calls: each snapshot row already stores the HubSpot-derived
region/type from snapshot time. The only things that changed in the fix are
deterministic, email-based layers (overrides, TLD, fallback). We re-apply just
those on top of each stored row, then reuse the production aggregation, rollup
and Notion upsert functions verbatim — so the backfill cannot drift from the
live weekly job.

Modes:
  DRY_RUN=1  -> print a per-week reconciliation summary, write nothing.
  (unset)    -> upsert recomputed rows into the two Notion DBs (idempotent on
                Email + Week of). Never posts to Slack.

Scope: the three ISO (Mon-Sun) weeks overlapping 2026-05-15 .. 2026-05-31:
  week_of 2026-05-11, 2026-05-18, 2026-05-25.
"""

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the deterministic classification layers from the daily job.
from daily_intercom_report import get_override, _email_tld_region

# Reuse the production aggregation / rollup / Notion functions from the weekly job.
import weekly_login_report as weekly

DRY_RUN = os.environ.get('DRY_RUN', '') not in ('', '0', 'false', 'False')

# Mondays of the ISO weeks overlapping 2026-05-15 .. 2026-05-31.
BACKFILL_WEEK_MONDAYS = ['2026-05-11', '2026-05-18', '2026-05-25']

BUCKETS = [
    ('Publisher',  'AU', 'ANZ', weekly.NOTION_DB_PUBLISHER),
    ('Publisher',  'UK', 'UK',  weekly.NOTION_DB_PUBLISHER),
    ('Advertiser', 'AU', 'ANZ', weekly.NOTION_DB_AGENCY),
    ('Advertiser', 'UK', 'UK',  weekly.NOTION_DB_AGENCY),
]


def reclassify_row(row):
    """Apply the fix's deterministic layers on top of a stored snapshot row.

    Mirrors daily_intercom_report.main():
      region: hard override -> (stored) -> TLD fill when stored is Unknown
      type:   hard override -> (stored) -> Unknown->Publisher fallback when region known
    Returns a shallow-copied row with corrected region/type.
    """
    email = (row.get('email') or '')
    region = row.get('region')
    rtype = row.get('type')
    overr = get_override(email)

    # Region
    if overr.get('region') in ('AU', 'UK'):
        region = overr['region']
    elif region not in ('AU', 'UK'):
        tld = _email_tld_region(email)
        if tld:
            region = tld

    # Type
    if overr.get('type') in ('Publisher', 'Advertiser'):
        rtype = overr['type']

    # Region-known / type-Unknown -> Publisher (matches the daily routing fallback).
    if region in ('AU', 'UK') and rtype == 'Unknown':
        rtype = 'Publisher'

    new = dict(row)
    new['region'] = region
    new['type'] = rtype
    return new


def corrected_snapshots(snapshots):
    """Return a {date: [reclassified rows]} copy of the snapshot store."""
    return {d: [reclassify_row(r) for r in rows] for d, rows in snapshots.items()}


def main():
    snapshots = weekly.load_snapshots()
    if not snapshots:
        print('No snapshots found. Nothing to backfill.')
        return

    fixed = corrected_snapshots(snapshots)

    named_domains = ('telegraph.co.uk', 'nytimes.com', 'pinknews.co.uk',
                     'newstatesman.co.uk', 'craftmedia.london')
    named_seen = defaultdict(set)  # domain -> set(week_of) where it appears in a UK bucket

    print(f"{'BACKFILL (DRY RUN)' if DRY_RUN else 'BACKFILL (WRITING TO NOTION)'}")
    print(f"Weeks: {', '.join(BACKFILL_WEEK_MONDAYS)}\n")

    schemas_ensured = set()
    grand_added = 0

    for monday in BACKFILL_WEEK_MONDAYS:
        ref = datetime.strptime(monday, '%Y-%m-%d').date()
        # window = Mon..Sun starting at this Monday
        from datetime import timedelta
        window = [(ref + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
        week_of = window[0]
        print(f"=== Week of {week_of} ({window[0]} .. {window[-1]}) ===")

        for team, region, region_label, db_id in BUCKETS:
            before = weekly.aggregate_users(snapshots, window, team, region)
            after  = weekly.aggregate_users(fixed,     window, team, region)
            before_emails = {u['email'] for u in before}
            after_emails  = {u['email'] for u in after}
            added = sorted(after_emails - before_emails)

            # Track named UK domains for the acceptance check.
            if region == 'UK':
                for u in after:
                    dom = (u['email'].split('@')[-1] if '@' in u['email'] else '').lower()
                    if dom in named_domains:
                        named_seen[dom].add(week_of)

            tag = f"{team[:3]} {region_label}"
            print(f"  {tag:10s} before={len(before):3d}  after={len(after):3d}  +{len(added)}")
            if added:
                for e in added:
                    print(f"       + {e}")
            grand_added += len(added)

            if not DRY_RUN and after:
                orgs = weekly.rollup_by_org(after)
                if db_id not in schemas_ensured:
                    weekly.ensure_database_schema(db_id)
                    schemas_ensured.add(db_id)
                weekly.upsert_user_rows(db_id, after, orgs, week_of, region_label)
                print(f"       -> upserted {len(after)} rows into Notion ({db_id})")
        print()

    print("─" * 60)
    print(f"Total distinct (email, bucket, week) rows newly recovered: {grand_added}")
    print("\nNamed-domain acceptance check (must each appear in >=1 UK week):")
    all_named_ok = True
    for dom in named_domains:
        weeks = sorted(named_seen.get(dom, []))
        status = 'OK' if weeks else 'MISSING'
        if not weeks:
            all_named_ok = False
        print(f"  {dom:22s} {status:8s} weeks={weeks}")
    print()
    if all_named_ok:
        print("All named UK domains recovered. ✔")
    else:
        print("WARNING: some named domains not recovered — investigate before writing.")

    if DRY_RUN:
        print("\nDRY RUN — no Notion writes performed. Set DRY_RUN=0 (or unset) to apply.")


if __name__ == '__main__':
    main()
