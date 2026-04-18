# Claude Routines

Personal automation routines running in Claude, triggered 
by schedule or events.

## Active routines

| Name | Trigger | Channel | Purpose |
|------|---------|---------|--------|
| Asana Review | Mon–Fri 16:00 AEST + ✏️ in #df | #df | Surface overdue tasks, bulk-execute via reactions |
| Email Triage | Mon–Fri 09:00 AEST + ✏️ in #df | #df | Scan Gmail, draft replies, action via reactions |

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
