import os
import json
import uuid
import fcntl
import time
import tempfile
from datetime import datetime, timedelta
from contextlib import contextmanager
import logging
from pathlib import Path

# Import atomic file ops with fallback for different run contexts
try:
    from .atomic_file_ops import load_json, save_json
except ImportError:
    from atomic_file_ops import load_json, save_json

# Setup
BASE_DIR = Path(__file__).parent
TASKS_FILE = BASE_DIR / "scheduled_tasks.json"


def _load_tasks():
    return load_json(TASKS_FILE, default=[])


def _save_tasks(tasks):
    save_json(TASKS_FILE, tasks)


@contextmanager
def _transact_tasks():
    """
    Hold the file lock for an entire read→modify→write transaction.

    Prevents TOCTOU races where concurrent callers (e.g. check_due_tasks vs
    add_task from different processes) read stale data and clobber each other's
    writes. Uses the same .json.lock file as atomic_file_ops so the two
    locking mechanisms are mutually exclusive.

    Yields a mutable list of tasks. On normal exit the list is written back
    atomically (temp-file + rename). On exception the write is skipped.
    """
    lock_path = TASKS_FILE.with_suffix(f'{TASKS_FILE.suffix}.lock')
    lock_fd = None
    start_time = time.time()
    timeout = 10.0

    # Acquire exclusive lock (same lock file as atomic_file_ops).
    # Use 'a' mode (not 'w') to avoid truncation races, and never unlink
    # the lock file so all contenders share the same stable inode.
    while True:
        try:
            lock_fd = open(lock_path, 'a')
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (IOError, OSError):
            if lock_fd:
                try:
                    lock_fd.close()
                except Exception:
                    pass
                lock_fd = None
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Could not acquire lock for {TASKS_FILE} within {timeout}s"
                )
            time.sleep(0.1)

    try:
        # --- Read (under lock, bypassing load_json's own lock) ---
        tasks = []
        if TASKS_FILE.exists():
            try:
                with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                    tasks = json.load(f)
            except (json.JSONDecodeError, Exception):
                tasks = []

        yield tasks

        # --- Write atomically (only reached on normal exit) ---
        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=TASKS_FILE.parent,
            prefix=f'.{TASKS_FILE.name}',
            suffix='.tmp',
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(tasks, f, indent=2, ensure_ascii=False, default=str)
        temp_path.replace(TASKS_FILE)
    finally:
        # Release lock but keep lock file (stable inode prevents races)
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def add_task(prompt, schedule_text, silent=False, task_type="prompt", agent=None, room_id=None, project=None):
    """
    Schedules a new task.
    prompt: The text to send to the agent.
    schedule_text: "every X minutes/hours", "daily at HH:MM", or "once at YYYY-MM-DDTHH:MM:SS"
    silent: If True, task runs in background without notifications or chat visibility.
            Use for maintenance tasks (Librarian, Gardener). Default: False.
    task_type: "prompt" (default) or "agent" for agent invocations.
    agent: Agent name if task_type is "agent".
    room_id: Optional room ID to target. If specified:
             - For 'prompt' tasks: Output will be delivered to this room with history context.
             - For 'agent' tasks: Agent output will be delivered to this room.
             If None, uses active room or creates new chat.
    project: Optional project tag (string or list of strings). When present, the dispatcher
             injects PROJECT METADATA into the agent's prompt so output gets tagged with
             YAML frontmatter for automatic routing to the project's _status.md.
    """
    with _transact_tasks() as tasks:
        new_task = {
            "id": str(uuid.uuid4())[:8],
            "prompt": prompt,
            "schedule": schedule_text,
            "created_at": datetime.now().isoformat(),
            "last_run": datetime.now().isoformat(),
            "active": True,
            "silent": silent,
            "type": task_type,
        }

        if task_type == "agent" and agent:
            new_task["agent"] = agent

        # Store room_id if provided for room-targeted delivery
        if room_id:
            new_task["room_id"] = room_id

        # Store project tag if provided for output routing
        if project:
            new_task["project"] = project

        tasks.append(new_task)
    mode = " (silent)" if silent else ""
    agent_info = f" via agent '{agent}'" if task_type == "agent" and agent else ""
    room_info = f" → room '{room_id}'" if room_id else ""
    project_info = f" [project: {project}]" if project else ""
    return f"✅ Task scheduled{mode}{agent_info}{room_info}{project_info} (ID: {new_task['id']}): '{prompt}' ({schedule_text})"


def add_agent_task(agent, prompt, schedule_text, room_id=None, silent=True, project=None):
    """
    Schedule an agent task.

    agent: Agent name (claude_code, information_gatherer, general_purpose, deep_think, librarian, gardener)
    prompt: Task description for the agent.
    schedule_text: "every X minutes/hours", "daily at HH:MM", or "once at YYYY-MM-DDTHH:MM:SS"
    room_id: Optional room ID to target. If specified, agent output will be delivered to this room.
             If None, output goes to 00_Inbox/agent_outputs/ for async review.
    silent: If True (default), runs in background without creating a visible chat or notifications.
            If False, creates a visible chat with notifications when the agent completes.
    project: Optional project tag (string or list of strings) for output routing.
    """
    return add_task(prompt, schedule_text, silent=silent, task_type="agent", agent=agent, room_id=room_id, project=project)

import re

def list_tasks(include_inactive=False):
    tasks = _load_tasks()
    if not tasks:
        return "No scheduled tasks found."

    # Filter by active status unless include_inactive is True
    if not include_inactive:
        tasks = [t for t in tasks if t.get('active', True)]

    if not tasks:
        return "No active scheduled tasks. Use include_all=true to see inactive tasks."

    output = ["📅 **Scheduled Tasks:**"]
    for t in tasks:
        status_icon = "🟢" if t.get('active', True) else "🔴"

        # Check for error status
        error_msg = t.get('last_error')
        if error_msg:
            status_icon = "⚠️"

        # Check for silent mode
        is_silent = t.get('silent', False)
        silent_indicator = " 🔇" if is_silent else ""

        # Check for agent task type
        task_type = t.get('type', 'prompt')
        agent_indicator = f" 🤖{t.get('agent', '?')}" if task_type == 'agent' else ""

        # Check for room targeting
        room_id = t.get('room_id')
        room_indicator = f" 📍{room_id[:8]}..." if room_id and len(room_id) > 8 else f" 📍{room_id}" if room_id else ""

        # Check for project tag
        project = t.get('project')
        project_indicator = f" 📂{project}" if project else ""

        last = t.get('last_run', 'Never')
        if last != 'Never':
            try:
                dt = datetime.fromisoformat(last)
                last = dt.strftime("%Y-%m-%d %H:%M")
            except:
                pass

        line = f"{status_icon} `{t['id']}`{silent_indicator}{agent_indicator}{project_indicator}{room_indicator}: {t['prompt']}\n   Schedule: {t['schedule']} (Last: {last})"
        if error_msg:
            line += f"\n   ❌ Error: {error_msg}"
        output.append(line)

    return "\n".join(output)

def remove_task(task_id):
    with _transact_tasks() as tasks:
        initial_count = len(tasks)
        tasks[:] = [t for t in tasks if t['id'] != task_id]

        if len(tasks) < initial_count:
            return f"✅ Task `{task_id}` removed."
    return f"❌ Task `{task_id}` not found."


def update_task(task_id, silent=None, active=None, schedule=None, prompt=None, room_id=None, project=None):
    """
    Update an existing scheduled task.
    task_id: The task ID to update.
    silent: Set to True/False to change silent mode.
    active: Set to True/False to enable/disable task.
    schedule: New schedule string.
    prompt: New prompt text.
    room_id: Set target room ID. Use empty string "" to clear room targeting.
    project: Set project tag (string or list). Use empty string "" to clear.
    """
    with _transact_tasks() as tasks:
        for t in tasks:
            if t['id'] == task_id:
                changes = []

                if silent is not None:
                    old_silent = t.get('silent', False)
                    t['silent'] = silent
                    changes.append(f"silent: {old_silent} → {silent}")

                if active is not None:
                    old_active = t.get('active', True)
                    t['active'] = active
                    changes.append(f"active: {old_active} → {active}")

                if schedule is not None:
                    old_schedule = t.get('schedule')
                    t['schedule'] = schedule
                    changes.append(f"schedule: '{old_schedule}' → '{schedule}'")

                if prompt is not None:
                    t['prompt'] = prompt
                    changes.append("prompt updated")

                if room_id is not None:
                    old_room = t.get('room_id')
                    if room_id == "":
                        # Clear room targeting
                        t.pop('room_id', None)
                        changes.append(f"room_id: '{old_room}' → (cleared)")
                    else:
                        t['room_id'] = room_id
                        changes.append(f"room_id: '{old_room}' → '{room_id}'")

                if project is not None:
                    old_project = t.get('project')
                    if project == "":
                        # Clear project tag
                        t.pop('project', None)
                        changes.append(f"project: '{old_project}' → (cleared)")
                    else:
                        t['project'] = project
                        changes.append(f"project: '{old_project}' → '{project}'")

                return f"✅ Task `{task_id}` updated: {', '.join(changes)}"

    return f"❌ Task `{task_id}` not found."

def check_due_tasks():
    """
    Checks tasks and returns a list of prompts that actually need to run NOW.
    Updates 'last_run' for those tasks immediately.

    The entire read→check→update→write is wrapped in _transact_tasks() so that
    concurrent callers (e.g. add_task from an MCP tool) cannot read a stale
    snapshot and clobber the last_run updates.
    """
    due_prompts = []

    with _transact_tasks() as tasks:
        now = datetime.now()

        for t in tasks:
            # Clear previous errors
            if 'last_error' in t:
                del t['last_error']

            if not t.get('active', True):
                continue

            should_run = False
            last_run_str = t.get('last_run')
            last_run = datetime.fromisoformat(last_run_str) if last_run_str else None

            schedule = t['schedule'].lower().strip()

            try:
                # 1. "every X minutes/hours/days"
                match_every = re.match(r"every\s+(\d+)?\s*(minute|hour|day)s?", schedule)

                # 2. "daily at HH:MM(am/pm)?"
                match_daily = re.search(r"daily at\s+(\d{1,2}):(\d{2})\s*(am|pm)?", schedule)

                # 3. "once at YYYY-MM-DD..."
                match_once = re.search(r"once at\s+(.+)", schedule)

                if match_every:
                    val = int(match_every.group(1)) if match_every.group(1) else 1
                    unit = match_every.group(2) # minute, hour, day

                    delta = None
                    if "minute" in unit:
                        delta = timedelta(minutes=val)
                    elif "hour" in unit:
                        delta = timedelta(hours=val)
                    elif "day" in unit:
                        delta = timedelta(days=val)

                    if delta:
                        if last_run is None:
                            should_run = True
                        elif now - last_run >= delta:
                            should_run = True

                elif match_daily:
                    hour = int(match_daily.group(1))
                    minute = int(match_daily.group(2))
                    meridiem = match_daily.group(3) # am/pm/None

                    # Handle 12-hour format
                    if meridiem:
                        if meridiem == "pm" and hour != 12:
                            hour += 12
                        elif meridiem == "am" and hour == 12:
                            hour = 0

                    target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    if now >= target_today:
                        if last_run is None or last_run < target_today:
                            should_run = True

                elif match_once:
                    target_str = match_once.group(1).strip()
                    target_dt = datetime.fromisoformat(target_str)

                    if now >= target_dt:
                        should_run = True
                        t['active'] = False

                else:
                    # Try cron syntax: "minute hour day-of-month month day-of-week"
                    cron_match = re.match(r'^([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)$', t['schedule'].strip())

                    if cron_match:
                        cron_min, cron_hour, cron_dom, cron_month, cron_dow = cron_match.groups()

                        def cron_field_matches(field, current_val):
                            if field == '*':
                                return True
                            if field.startswith('*/'):
                                step = int(field[2:])
                                return current_val % step == 0
                            for part in field.split(','):
                                part = part.strip()
                                if '/' in part:
                                    range_part, step = part.split('/', 1)
                                    step = int(step)
                                    if '-' in range_part:
                                        lo, hi = range_part.split('-', 1)
                                        lo, hi = int(lo), int(hi)
                                        if lo <= current_val <= hi and (current_val - lo) % step == 0:
                                            return True
                                elif '-' in part:
                                    lo, hi = part.split('-', 1)
                                    lo, hi = int(lo), int(hi)
                                    if lo <= current_val <= hi:
                                        return True
                                else:
                                    if int(part) == current_val:
                                        return True
                            return False

                        min_ok = cron_field_matches(cron_min, now.minute)
                        hour_ok = cron_field_matches(cron_hour, now.hour)
                        dom_ok = cron_field_matches(cron_dom, now.day)
                        month_ok = cron_field_matches(cron_month, now.month)
                        python_dow = (now.weekday() + 1) % 7
                        dow_ok = cron_field_matches(cron_dow, python_dow)

                        if min_ok and hour_ok and dom_ok and month_ok and dow_ok:
                            if last_run is None:
                                should_run = True
                            else:
                                this_minute_start = now.replace(second=0, microsecond=0)
                                if last_run < this_minute_start:
                                    should_run = True
                        else:
                            # CATCH-UP LOGIC for daily cron jobs
                            if cron_min != '*' and cron_hour != '*' and cron_dom == '*' and cron_month == '*':
                                scheduled_hour = int(cron_hour)
                                scheduled_min = int(cron_min)

                                today_target = now.replace(hour=scheduled_hour, minute=scheduled_min, second=0, microsecond=0)

                                dow_matches_today = cron_field_matches(cron_dow, python_dow)

                                if dow_matches_today and now > today_target:
                                    if last_run is None or last_run < today_target:
                                        hours_since_target = (now - today_target).total_seconds() / 3600
                                        if hours_since_target <= 6:
                                            should_run = True
                                            logging.getLogger(__name__).info(
                                                f"Catch-up: Running missed cron task '{t.get('id')}' "
                                                f"(scheduled {scheduled_hour}:{scheduled_min:02d}, "
                                                f"now {now.strftime('%H:%M')}, {hours_since_target:.1f}h late)"
                                            )
                    else:
                        # Unrecognized format
                        t['last_error'] = f"Unrecognized schedule format: '{t['schedule']}'"

            except Exception as e:
                t['last_error'] = f"Parsing error: {str(e)}"

            if should_run:
                task_type = t.get('type', 'prompt')
                task_info = {
                    "id": t.get('id'),
                    "type": task_type,
                    "silent": t.get('silent', False)
                }

                if t.get('room_id'):
                    task_info["room_id"] = t['room_id']

                if t.get('project'):
                    task_info["project"] = t['project']

                if task_type == "agent":
                    task_info["agent"] = t.get('agent')
                    task_info["prompt"] = t['prompt']
                else:
                    task_info["prompt"] = f"👇 [SCHEDULED AUTOMATION] 👇\n{t['prompt']}"

                due_prompts.append(task_info)
                t['last_run'] = now.isoformat()

    return due_prompts

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(list_tasks())
    else:
        cmd = sys.argv[1]
        if cmd == "add":
            # python scheduler_tool.py add "prompt" "schedule"
            if len(sys.argv) >= 4:
                # Join remaining args for prompt if schedule is last?
                # Usage: add "Prompt string" "every 5 minutes"
                # argv[0]=script, argv[1]=add, argv[2]=Prompt, argv[3]=Schedule
                print(add_task(sys.argv[2], sys.argv[3]))
            else:
                print("Usage: add <prompt> <schedule>")
        elif cmd == "list":
            print(list_tasks())
        elif cmd == "remove":
            if len(sys.argv) >= 3:
                print(remove_task(sys.argv[2]))
        elif cmd == "check":
            # For internal use mostly
            print(check_due_tasks())
