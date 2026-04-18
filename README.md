# Claude Routines

Personal automation routines running in Claude, triggered by schedule or events.

## Active routines

| Name | Trigger | Channel | Purpose |
|------|---------|---------|--------|
| Asana Review | Mon–Fri 16:00 AEST + ✏️ in #df | #df | Surface overdue tasks, bulk-execute via reactions |
| Email Triage | Mon–Fri 09:00 AEST + ✏️ in #df | #df | Scan Gmail, draft replies, action via reactions |

## How it works

Routines follow a two-phase pattern:
- **Phase 1** — scheduled scan. Surfaces items to Slack as individual messages.
- **Phase 2** — triggered by ✏️ posted in #df. Bulk-executes based on emoji reactions on individual messages.

Emoji reaction system:
| Emoji | Asana routine | Email routine |
|-------|--------------|---------------|
| 👍 | +3 days | Send as-is |
| 🤝 | +7 days | Save to Gmail drafts |
| 😆 | Mark complete | Snooze to tomorrow |

## Repo structure

Each routine lives in its own folder containing:
- `routine.md` — the full prompt pasted into Claude
- `CHANGELOG.md` — log of tweaks and why

## Principles

- Routines are version-controlled here but execute via MCP connectors in Claude sessions
- Never auto-send or auto-execute without a human reaction
- When in doubt, surface to Slack and wait — don't act
- Edit `routine.md` here first, then update the Claude routine from this file
