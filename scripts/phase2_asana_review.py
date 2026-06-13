#!/usr/bin/env python3
"""
Phase 2 - Asana Review Bulk Executor
Triggered by GitHub Actions when the trigger file is updated.
Reads today's review thread in #df, checks emoji reactions, updates Asana tasks.
"""

import os
import re
import sys
from datetime import date, timedelta, datetime

import requests

SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]
ASANA_TOKEN = os.environ["ASANA_PAT"]
CHANNEL_ID = "C0AUC27V9Q8"   # #df

SLACK_HEADERS = {"Authorization": f"Bearer {SLACK_TOKEN}"}
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}",
                 "Content-Type": "application/json"}


def next_weekday(d):
    """Push d to Monday if it falls on Sat/Sun."""
    if d.weekday() == 5:    # Saturday
        return d + timedelta(days=2)
    if d.weekday() == 6:    # Sunday
        return d + timedelta(days=1)
    return d


def parse_display_date(s):
    """Parse 'Mon 20 Apr' -> date. Returns None for N/A or blank."""
    if not s or s.strip().upper() in ("N/A", "NONE", "-"):
        return None
    s = s.strip()
    year = date.today().year
    for fmt in ("%a %d %b", "%d %b"):
        try:
            return datetime.strptime(f"{s} {year}", fmt + " %Y").date()
        except ValueError:
            pass
    return None


def slack_get(method, **params):
    r = requests.get(
        f"https://slack.com/api/{method}",
        headers=SLACK_HEADERS,
        params=params,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method} error: {data.get('error')}")
    return data


def slack_post_msg(payload):
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={**SLACK_HEADERS, "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack chat.postMessage error: {data.get('error')}")
    return data


def asana_update(task_gid, payload):
    r = requests.put(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        json={"data": payload},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["data"]


def find_latest_review_thread(lookback_days=3):
    """Return thread_ts for the most recent Daily Asana Review post in #df
    within the last `lookback_days` days. Looking back rather than only at
    today lets a weekend or next-morning :rocket: still find the latest
    review (e.g. Friday's review actioned on Saturday or Monday morning).
    conversations.history returns newest-first, so the first match is the
    most recent review."""
    oldest = (datetime.now() - timedelta(days=lookback_days)).timestamp()
    resp = slack_get(
        "conversations.history",
        channel=CHANNEL_ID,
        oldest=str(oldest),
        limit=100,
    )
    for msg in resp.get("messages", []):
        if "Daily Asana Review" in msg.get("text", ""):
            return msg["ts"]
    return None


def get_thread_replies(thread_ts):
    resp = slack_get(
        "conversations.replies",
        channel=CHANNEL_ID,
        ts=thread_ts,
        limit=200,
    )
    messages = resp.get("messages", [])
    return [m for m in messages[1:] if m.get("ts") != thread_ts]


def parse_task_message(msg):
    text = msg.get("text", "")

    gid_match = re.search(r"https://app\.asana\.com/0/\d+/(\d+)", text)
    task_gid = gid_match.group(1) if gid_match else None

    cur_match = re.search(r"Current due date:\s*(.+?)(?:\n|$)", text)
    current_due = parse_display_date(cur_match.group(1).strip()) if cur_match else None

    sug_match = re.search(r"Suggested date:\s*(.+?)(?:\n|$)", text)
    suggested_date = parse_display_date(sug_match.group(1).strip()) if sug_match else None

    name_match = re.search(r"Task:\s*(.+?)(?:\n|$)", text)
    task_name = name_match.group(1).strip() if name_match else "Unknown task"

    return {
        "ts": msg["ts"],
        "task_gid": task_gid,
        "task_name": task_name,
        "current_due": current_due,
        "suggested_date": suggested_date,
    }


def get_reactions(msg_ts):
    try:
        resp = slack_get("reactions.get", channel=CHANNEL_ID, timestamp=msg_ts, full=True)
        return {r["name"] for r in resp.get("message", {}).get("reactions", [])}
    except Exception:
        return set()


def main():
    today = date.today()
    print(f"Phase 2 executing for {today}")

    thread_ts = find_latest_review_thread()
    if not thread_ts:
        msg = "Could not find a recent review thread in #df (last 3 days). Nothing to execute."
        print(f"ERROR: {msg}")
        slack_post_msg({"channel": CHANNEL_ID, "text": f":warning: Phase 2 failed: {msg}"})
        sys.exit(1)

    replies = get_thread_replies(thread_ts)
    print(f"Found {len(replies)} task messages in thread")

    rescheduled, closed, unchanged, skipped = [], [], [], []

    for msg in replies:
        task = parse_task_message(msg)
        if not task["task_gid"]:
            continue

        reactions = get_reactions(msg["ts"])
        name = task["task_name"]
        print(f"  {name}: reactions={reactions}")

        try:
            if "laughing" in reactions:
                asana_update(task["task_gid"], {"completed": True})
                closed.append(name)
                print(f"    -> closed")

            elif "thumbsup" in reactions and task["current_due"]:
                new_due = next_weekday(task["current_due"] + timedelta(days=3))
                asana_update(task["task_gid"], {"due_on": new_due.isoformat()})
                rescheduled.append((name, new_due.strftime("%a %d %b")))
                print(f"    -> rescheduled to {new_due}")

            elif "handshake" in reactions and task["current_due"]:
                new_due = next_weekday(task["current_due"] + timedelta(days=7))
                asana_update(task["task_gid"], {"due_on": new_due.isoformat()})
                rescheduled.append((name, new_due.strftime("%a %d %b")))
                print(f"    -> rescheduled to {new_due}")

            elif not reactions and task["suggested_date"]:
                new_due = next_weekday(task["suggested_date"])
                asana_update(task["task_gid"], {"due_on": new_due.isoformat()})
                unchanged.append((name, new_due.strftime("%a %d %b")))
                print(f"    -> accepted suggested date {new_due}")

            else:
                skipped.append(name)
                print(f"    -> skipped (needs manual decision)")

        except Exception as e:
            print(f"    ERROR: {e}")
            skipped.append(f"{name} (error: {e})")

    # Post summary
    lines = [
        ":white_check_mark: Bulk execution complete — tasks updated successfully.",
        f"• :calendar: Rescheduled: {len(rescheduled)}",
        f"• :white_check_mark: Closed: {len(closed)}",
        f"• — Unchanged: {len(unchanged)}",
        "",
    ]
    if closed:
        lines.append("Closed:")
        lines.extend(f"• {n}" for n in closed)
        lines.append("")
    if rescheduled:
        lines.append("Rescheduled:")
        lines.extend(f"• {n} → {d}" for n, d in rescheduled)
        lines.append("")
    if unchanged:
        lines.append("Accepted suggested date:")
        lines.extend(f"• {n} → {d}" for n, d in unchanged)
        lines.append("")
    if skipped:
        lines.append("Skipped — needs your decision:")
        lines.extend(f"• {s}" for s in skipped)
        lines.append("")

    # Next weekday after today
    days_ahead = (7 - today.weekday()) % 7 or 7
    next_review = (today + timedelta(days=days_ahead)).strftime("%a %d %b")
    lines.append(f"Next review: {next_review} at 16:00")

    summary = "\n".join(lines)
    print(f"\nSummary:\n{summary}")
    slack_post_msg({"channel": CHANNEL_ID, "thread_ts": thread_ts, "text": summary})
    print("Done.")


if __name__ == "__main__":
    main()
