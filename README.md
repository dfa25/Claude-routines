# Claude Routines

Personal automation routines running in Claude, triggered 
by schedule or events.

## Active routines

| Name | Trigger | Channel | Purpose |
|------|---------|---------|--------|
| Asana Review | Mon–Fri 16:00 AEST + ✏️ in #df | #df | Surface overdue tasks, bulk-execute via reactions |
| Email Triage | Mon–Fri 09:00 AEST + ✏️ in #df | #df | Scan Gmail, draft replies, action via reactions |
| Daily Intercom Report | Daily 12:00 AEST / 12:00 BST | 4 activity channels | Post last-24h logins (unique users + total logins) per region/team; persist daily snapshot |
| Weekly Login Report | Fri 09:00 AEST (AU) · Thu 09:00 BST (UK) | 4 activity channels + 2 Notion DBs | Weekly rollup: users, sessions, new vs returning, org penetration |

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

Two scripts, one shared snapshot store:

1. **`scripts/daily_intercom_report.py`** — pulls active Intercom contacts,
   enriches via HubSpot, posts per-bucket summaries to Slack, and writes a
   daily snapshot to `data/snapshots/YYYY-MM-DD.json` (committed back to the
   repo by the workflow).
2. **`scripts/weekly_login_report.py`** — reads the last 7 snapshots, builds
   per-user metrics (sessions, days active, new vs returning, last login),
   rolls up by organisation, upserts rows into two Notion databases
   (Publisher + Agency), and posts a weekly summary to each of the 4 Slack
   channels.

Backfill: run either daily workflow with `LOOKBACK_HOURS=720` via
`workflow_dispatch` to seed a 30-day snapshot before the first Friday run.
