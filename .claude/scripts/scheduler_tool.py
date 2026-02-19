import os
import uuid
from datetime import datetime, timedelta
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
    tasks = _load_tasks()
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
    _save_tasks(tasks)
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
    tasks = _load_tasks()
    initial_count = len(tasks)
    tasks = [t for t in tasks if t['id'] != task_id]

    if len(tasks) < initial_count:
        _save_tasks(tasks)
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
    tasks = _load_tasks()
    found = False

    for t in tasks:
        if t['id'] == task_id:
            found = True
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

            _save_tasks(tasks)
            return f"✅ Task `{task_id}` updated: {', '.join(changes)}"

    if not found:
        return f"❌ Task `{task_id}` not found."

def check_due_tasks():
    """
    Checks tasks and returns a list of prompts that actually need to run NOW.
    Updates 'last_run' for those tasks immediately.
    """
    tasks = _load_tasks()
    due_prompts = []
    dirty = False
    
    now = datetime.now()
    
    for t in tasks:
        # Clear previous errors
        if 'last_error' in t:
            del t['last_error']
            dirty = True

        if not t.get('active', True):
            continue
            
        should_run = False
        last_run_str = t.get('last_run')
        last_run = datetime.fromisoformat(last_run_str) if last_run_str else None
        
        schedule = t['schedule'].lower().strip()
        
        try:
            # 1. "every X minutes/hours/days"
            # Regex: every (number)? (minute|hour|day)s?
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
                
                # If target is in the future today, we wait.
                # If target is in the past today:
                #   If last_run was BEFORE today's target, run.
                #   If last_run was AFTER today's target (i.e. we already ran today), don't run.
                
                if now >= target_today:
                    # It's time or past time today.
                    # Check if we ran today since the target time.
                    if last_run is None or last_run < target_today:
                        should_run = True

            elif match_once:
                target_str = match_once.group(1).strip()
                # Try strict ISO, then fuzzy if needed? Keeping strict for now
                target_dt = datetime.fromisoformat(target_str)

                if now >= target_dt:
                    should_run = True
                    t['active'] = False
                    dirty = True

            else:
                # Try cron syntax: "minute hour day-of-month month day-of-week"
                # e.g., "30 17 * * *" = daily at 5:30 PM
                # Extended cron regex: each field can be *, a number, or contain commas/dashes/slashes
                cron_match = re.match(r'^([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)$', t['schedule'].strip())

                if cron_match:
                    cron_min, cron_hour, cron_dom, cron_month, cron_dow = cron_match.groups()

                    # Check if current time matches cron pattern
                    # Supports: *, single values, comma-separated (1,3,5), ranges (1-5), steps (*/2, 1-5/2)
                    def cron_field_matches(field, current_val):
                        if field == '*':
                            return True
                        # Handle step on wildcard: */N
                        if field.startswith('*/'):
                            step = int(field[2:])
                            return current_val % step == 0
                        # Comma-separated: check each part
                        for part in field.split(','):
                            part = part.strip()
                            if '/' in part:
                                # range/step: e.g. 1-5/2
                                range_part, step = part.split('/', 1)
                                step = int(step)
                                if '-' in range_part:
                                    lo, hi = range_part.split('-', 1)
                                    lo, hi = int(lo), int(hi)
                                    if lo <= current_val <= hi and (current_val - lo) % step == 0:
                                        return True
                            elif '-' in part:
                                # range: e.g. 1-5
                                lo, hi = part.split('-', 1)
                                lo, hi = int(lo), int(hi)
                                if lo <= current_val <= hi:
                                    return True
                            else:
                                # single value
                                if int(part) == current_val:
                                    return True
                        return False

                    # Check all fields against current time
                    min_ok = cron_field_matches(cron_min, now.minute)
                    hour_ok = cron_field_matches(cron_hour, now.hour)
                    dom_ok = cron_field_matches(cron_dom, now.day)
                    month_ok = cron_field_matches(cron_month, now.month)
                    # weekday: cron uses 0=Sunday, Python uses 0=Monday
                    # Convert Python's weekday (Mon=0) to cron (Sun=0): (weekday + 1) % 7
                    python_dow = (now.weekday() + 1) % 7
                    dow_ok = cron_field_matches(cron_dow, python_dow)

                    if min_ok and hour_ok and dom_ok and month_ok and dow_ok:
                        # We're in the right minute - but did we already run this minute?
                        if last_run is None:
                            should_run = True
                        else:
                            # Only run if last_run was before this minute started
                            this_minute_start = now.replace(second=0, microsecond=0)
                            if last_run < this_minute_start:
                                should_run = True
                    else:
                        # CATCH-UP LOGIC: Check if we missed the scheduled time
                        # Only for TRUE daily cron jobs (specific hour/minute, wildcards for day/month)
                        # Don't catch up for specific-date schedules (like "0 1 28 1 *")
                        if cron_min != '*' and cron_hour != '*' and cron_dom == '*' and cron_month == '*':
                            scheduled_hour = int(cron_hour)
                            scheduled_min = int(cron_min)

                            # Calculate today's scheduled time
                            today_target = now.replace(hour=scheduled_hour, minute=scheduled_min, second=0, microsecond=0)

                            # Check day-of-week constraint if specified
                            dow_matches_today = cron_field_matches(cron_dow, python_dow)

                            # If we're past the scheduled time today, and haven't run since the target
                            if dow_matches_today and now > today_target:
                                if last_run is None or last_run < today_target:
                                    # Missed run - catch up! But add a grace period (6 hours)
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
                    dirty = True

        except Exception as e:
            t['last_error'] = f"Parsing error: {str(e)}"
            dirty = True

        if should_run:
            # Return task metadata along with prompt
            task_type = t.get('type', 'prompt')
            task_info = {
                "id": t.get('id'),
                "type": task_type,
                "silent": t.get('silent', False)  # Default to non-silent (visible)
            }

            # Include room_id if specified for room-targeted delivery
            if t.get('room_id'):
                task_info["room_id"] = t['room_id']

            # Include project tag if specified for output routing
            if t.get('project'):
                task_info["project"] = t['project']

            if task_type == "agent":
                # Agent task - return agent name and prompt
                task_info["agent"] = t.get('agent')
                task_info["prompt"] = t['prompt']
            else:
                # Prompt task - format for Claude <3
                task_info["prompt"] = f"👇 [SCHEDULED AUTOMATION] 👇\n{t['prompt']}"

            due_prompts.append(task_info)
            t['last_run'] = now.isoformat()
            dirty = True

    if dirty:
        _save_tasks(tasks)

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
