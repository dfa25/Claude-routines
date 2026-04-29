# Claude Routines

Personal automation routines running in Claude, triggered 
by schedule or events.

## Active routines

| Name | Trigger | Channel | Purpose |
|------|---------|---------|--------|
| Asana Review | Mon–Fri 16:00 AEST + ✏️ in #df | #df | Surface overdue tasks, bulk-execute via reactions |
| Email Triage | Mon–Fri 09:00 AEST + ✏️ in #df | #df | Scan Gmail, draft replies, action via reactions |
| Daily Intercom Report | Daily 12:00 AEST / 12:00 BST | 4 activity channels | Post last-24h logins (unique users + total logins) per region/team; persist daily snapshot |
| Weekly Login Report | Mon 09:00 AEST (AU, recap Mon–Sun) · Thu 09:00 BST (UK, recap Fri–Thu) | 4 activity channels + 2 Notion DBs | Weekly rollup: users, sessions, new vs returning, org penetration |

## Two-phase pattern

Every routine follows the same structure:

- **Phase 1** — runs on a schedule. Scans for items, 
  surfaces them to #df as individual messages with 
  emoji reaction options.
- **Phase 2** — triggered by posting ✏️ (:pencil2:) 
  in #df. Bulk-executes based on emoji reactions on 
  individual messages.

Using ✏️ (not 🚀) as the Phase 2 trigger across all 
routines — keeps it consistent and avoids emoji 
conflicts between routines in the same channel.

## Emoji reaction system

| Emoji | Asana routine | Email routine |
|-------|--------------|---------------|
| 👍 | +3 days | Send as-is |
| 🤝 | +7 days | Save to Gmail drafts |
| 😆 | Mark complete | Snooze to tomorrow |
| ✏️ | Trigger Phase 2 | Trigger Phase 2 |

## Repo structure

Each routine lives in its own folder:
- `routine.md` — full prompt pasted into Claude
- `CHANGELOG.md` — log of every tweak and why

## How to update a routine

1. Edit `routine.md` in this repo first
2. Copy the updated prompt into the Claude routine
3. Log the change in `CHANGELOG.md` with today's date
   and the reason

## Principles

- Routines execute via MCP connectors in Claude sessions
- The repo is the source of truth — edit here, not in Claude
- Never auto-send or auto-execute without a human reaction
- When in doubt, surface to Slack and wait — don't act

## Login tracking pipeline

Two workflows, one shared snapshot store, two destinations.

### Schedule

| Workflow | Cron (UTC) | Local time | Region filter |
|---|---|---|---|
| `daily-intercom-report-au.yml` | `0 2 * * *` daily | 12pm Sydney (1pm AEDT) | `REGION=AU` |
| `daily-intercom-report-uk.yml` | `0 11 * * *` daily | 12pm London (1pm GMT) | `REGION=UK` |
| `weekly-login-report-au.yml` | `0 23 * * 0` | Mon 9am Sydney (10am AEDT) — recap previous Mon–Sun | `REGION=AU` |
| `weekly-login-report-uk.yml` | `0 8 * * 4` | Thu 9am London (8am GMT) — recap Fri–Thu | `REGION=UK` |

Crons are UTC, so local time drifts ±1h with daylight saving — tolerated.

### Daily flow (runs twice a day — once per region)

1. **Fetch** Intercom contacts whose `last_seen_at` falls in the last
   `LOOKBACK_HOURS` (default `24`; override via `workflow_dispatch` for
   backfill).
2. **Enrich** each contact via HubSpot: company name + id, country/office,
   deal pipelines → classify `region` (AU/UK/Unknown) and `type`
   (Publisher/Advertiser/Unknown). Internal emails are skipped.
3. **Compute `logins_today`** per user = current `session_count` minus the
   same user's `session_count` in the previous daily snapshot (or `1` if
   never seen before).
4. **Write snapshot** to `data/snapshots/YYYY-MM-DD.json`. Both region runs
   on the same day merge into one file keyed by email. Committed back to
   `main` by the workflow (`contents: write` permission).
5. **Post to Slack** — four channels, one per (region × type) bucket:
   - `#mediaowner-login-activity-anz` — Publishers, AU
   - `#mediaowner-login-activity-uk` — Publishers, UK
   - `#advertiser-activity-au` — Advertisers, AU
   - `#advertiser-activity-uk` — Advertisers, UK

   Each post lists every active user with their login count today and ends
   with `Unique users: N` + `Total logins: M`. Slack post is skipped when
   `LOOKBACK_HOURS != 24` (backfill mode).

### Weekly flow (runs once per region, Thu UK / Fri AU)

1. **Load** every snapshot in `data/snapshots/`.
2. **Build window** — region-specific:
   - **AU** (run Mon 9am AEST): the previous Mon–Sun (7 UTC days ending yesterday)
   - **UK** (run Thu 9am BST): rolling Fri–Thu (7 UTC days ending today; today's
     daily snapshot may not have run yet, so the Thursday end-day can be partial)
3. **Per-user metrics** for each (region × type) bucket:
   - `Last login` = max `last_seen_at` in window
   - `Logins (7d)` = count of distinct days user appeared in a daily snapshot
     (Intercom's `session_count` isn't populated reliably, so we signal on
     `last_seen_at` appearing in a given day's snapshot instead)
   - `User type` = `New` if `created_at` inside the window, else `Returning`
4. **Per-org rollup** grouped by `company_name`:
   - Active users, new users, returning users
   - Total logins split new vs returning
   - Total users at org via HubSpot associated-contacts count
   - `Org penetration %` = active / total
5. **Upsert Notion** — two databases in the "user data" workspace:
   - Publisher DB: `34a789ce423180c19404f458b5d566c5`
   - Agency DB: `34a789ce423180b0b670c0971db144df`

   Schema is created idempotently on first run. Rows upserted on
   `Email + Week of` so re-runs don't duplicate.
6. **Post to Slack** — same four channels. Header line shows total unique
   users, total logins split new vs returning, active orgs count.
   Organisations listed in descending session order with each user
   underneath.

### Secrets (GitHub → Settings → Secrets → Actions)

- `INTERCOM_ACCESS_TOKEN`
- `HUBSPOT_ACCESS_TOKEN`
- `SLACK_BOT_TOKEN`
- `NOTION_TOKEN` (weekly only; integration must be shared with both Notion DBs)

### Backfill

One-off: Actions tab → **Daily New User Report — AU** (or UK) → **Run
workflow** → set *Hours of history to pull* to `720` → Run. Seeds the
snapshot store with everyone seen in the last 30 days and their current
`session_count`. Gives the next weekly run an accurate baseline instead
of estimated.
