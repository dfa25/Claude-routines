import os
import requests
from datetime import datetime, timedelta, timezone

# ── Slack channel ────────────────────────────────────────────────────────────
ASANA_CHANNEL = 'C0AUC27V9Q8'  # #df-asana

# ── API credentials ──────────────────────────────────────────────────────────
ASANA_TOKEN = os.environ['ASANA_ACCESS_TOKEN']
SLACK_TOKEN = os.environ['SLACK_BOT_TOKEN']

# ── Asana workspace/user config ──────────────────────────────────────────────
ASANA_WORKSPACE_GID = os.environ.get('ASANA_WORKSPACE_GID', '')
ASANA_ASSIGNEE_GID = os.environ.get('ASANA_ASSIGNEE_GID', 'me')


def _asana_headers():
    return {
        'Authorization': f'Bearer {ASANA_TOKEN}',
        'Content-Type': 'application/json',
    }


def get_overdue_tasks():
    """Fetch tasks assigned to user that are overdue (due_on < today)."""
    today = datetime.now(timezone.utc).date()

    params = {
        'assignee': ASANA_ASSIGNEE_GID,
        'completed_since': 'now',  # Only incomplete tasks
        'opt_fields': 'name,due_on,due_at,completed,permalink_url,projects.name',
    }
    if ASANA_WORKSPACE_GID:
        params['workspace'] = ASANA_WORKSPACE_GID

    resp = requests.get(
        'https://app.asana.com/api/1.0/tasks',
        headers=_asana_headers(),
        params=params,
    )
    resp.raise_for_status()

    tasks = resp.json().get('data', [])
    overdue = []

    for task in tasks:
        if task.get('completed'):
            continue
        due_on = task.get('due_on')
        if not due_on:
            continue
        due_date = datetime.strptime(due_on, '%Y-%m-%d').date()
        if due_date < today:
            overdue.append(task)

    return overdue


def calculate_suggested_date(due_date_str):
    """Calculate suggested new date: push to next Monday if weekend, else +1 business day."""
    due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
    today = datetime.now(timezone.utc).date()

    # If today is Saturday (5) or Sunday (6), push to Monday
    if today.weekday() == 5:  # Saturday
        return today + timedelta(days=2)
    elif today.weekday() == 6:  # Sunday
        return today + timedelta(days=1)
    else:
        # Weekday: push to today or next business day
        return today


def update_task_due_date(task_gid, new_due_date):
    """Update a task's due date in Asana."""
    resp = requests.put(
        f'https://app.asana.com/api/1.0/tasks/{task_gid}',
        headers=_asana_headers(),
        json={'data': {'due_on': new_due_date.strftime('%Y-%m-%d')}},
    )
    resp.raise_for_status()
    return resp.json()


def post_to_slack(channel_id, text):
    """Post a message to Slack."""
    resp = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': channel_id, 'text': text, 'mrkdwn': True},
    )
    data = resp.json()
    if not data.get('ok'):
        raise RuntimeError(f'Slack post failed: {data.get("error")}')


def main():
    print('Fetching overdue Asana tasks...')
    overdue_tasks = get_overdue_tasks()
    print(f'Found {len(overdue_tasks)} overdue task(s)')

    if not overdue_tasks:
        print('No overdue tasks to update.')
        return

    updated = []
    errors = []

    for task in overdue_tasks:
        task_gid = task['gid']
        task_name = task.get('name', 'Unnamed task')
        old_due = task.get('due_on', 'No date')
        new_date = calculate_suggested_date(old_due)

        try:
            update_task_due_date(task_gid, new_date)
            updated.append({
                'name': task_name,
                'old_due': old_due,
                'new_due': new_date.strftime('%Y-%m-%d'),
                'url': task.get('permalink_url', ''),
            })
            print(f'  Updated: {task_name} ({old_due} -> {new_date})')
        except Exception as e:
            errors.append({'name': task_name, 'error': str(e)})
            print(f'  Error updating {task_name}: {e}')

    # Post summary to Slack
    today_str = datetime.now(timezone.utc).strftime('%d %b %Y')
    lines = [
        f':white_check_mark: *Bulk Execute Complete* | {today_str}',
        '',
        f'*{len(updated)}* task(s) updated:',
    ]
    for t in updated[:10]:  # Show first 10
        lines.append(f"  - {t['name']} ({t['old_due']} -> {t['new_due']})")
    if len(updated) > 10:
        lines.append(f'  _...and {len(updated) - 10} more_')

    if errors:
        lines.append('')
        lines.append(f':warning: *{len(errors)}* task(s) failed to update')

    post_to_slack(ASANA_CHANNEL, '\n'.join(lines))
    print(f'\nPosted summary to Slack. {len(updated)} updated, {len(errors)} errors.')


if __name__ == '__main__':
    main()
