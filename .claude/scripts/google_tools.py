import os.path
import json
import argparse
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
    # Gmail scopes
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    # YouTube Music scopes
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]

# Calculate paths relative to this script
# Script is in .agent/scripts/
# Secrets are in .agent/secrets/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(SCRIPT_DIR)
SECRETS_DIR = os.path.join(AGENT_DIR, "secrets")

CREDENTIALS_FILE = os.path.join(SECRETS_DIR, "credentials.json")
TOKEN_FILE = os.path.join(SECRETS_DIR, "token.json")

def authenticate(headless=True):
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # If refresh fails, fall through to re-auth
                creds = None

        if not creds:
            if headless:
                 raise RuntimeError(f"Google Token is missing, invalid, or expired. Run '{os.path.basename(__file__)} --auth' on a machine with a browser to refresh.")

            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(f"Missing {CREDENTIALS_FILE}.")
            
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
            
    return creds

def create_tasks(service, tasks):
    results = []
    if not tasks: return results
    print(f"--- Processing {len(tasks)} Tasks ---")
    for task in tasks:
        body = {'title': task.get('title'), 'notes': task.get('notes', '')}
        if task.get('due'): body['due'] = task.get('due')
        try:
            res = service.tasks().insert(tasklist='@default', body=body).execute()
            print(f"Task Created: {res.get('title')}")
            results.append(res)
        except Exception as e:
            print(f"Task Error ({task.get('title')}): {e}")
    return results

def delete_task(service, task_id):
    """Delete a task by ID."""
    try:
        service.tasks().delete(tasklist='@default', task=task_id).execute()
        print(f"Task Deleted: {task_id}")
        return {"success": True, "task_id": task_id}
    except Exception as e:
        print(f"Delete Error ({task_id}): {e}")
        return {"success": False, "error": str(e)}

def normalize_due_date(due_str):
    """Convert YYYY-MM-DD to RFC 3339 format if needed."""
    if not due_str:
        return due_str
    # Already in RFC 3339 format
    if 'T' in due_str:
        return due_str
    # Convert YYYY-MM-DD to YYYY-MM-DDTHH:MM:SS.000Z
    return f"{due_str}T00:00:00.000Z"

def update_task(service, task_id, title=None, notes=None, due=None, status=None):
    """Update a task's fields. Only provided fields are updated."""
    try:
        # First get the existing task
        task = service.tasks().get(tasklist='@default', task=task_id).execute()

        # Update only provided fields
        if title is not None:
            task['title'] = title
        if notes is not None:
            task['notes'] = notes
        if due is not None:
            task['due'] = normalize_due_date(due)
        if status is not None:
            task['status'] = status

        result = service.tasks().update(tasklist='@default', task=task_id, body=task).execute()
        print(f"Task Updated: {result.get('title')} (ID: {task_id})")
        return {"success": True, "task": result}
    except Exception as e:
        print(f"Update Error ({task_id}): {e}")
        return {"success": False, "error": str(e)}

def create_events(service, events):
    results = []
    if not events: return results
    print(f"--- Processing {len(events)} Events ---")
    for event in events:
        start_val = event.get('start')
        end_val = event.get('end')
        
        # Simple heuristic: ISO dates (YYYY-MM-DD) are 10 chars. 
        # Everything else (ISO timestamps) is treated as dateTime.
        start_type = 'date' if len(start_val) == 10 else 'dateTime'
        end_type = 'date' if len(end_val) == 10 else 'dateTime'

        start_body = {start_type: start_val}
        end_body = {end_type: end_val}

        # Only add TimeZone for dateTime
        if start_type == 'dateTime': start_body['timeZone'] = 'America/Chicago'
        if end_type == 'dateTime': end_body['timeZone'] = 'America/Chicago'

        body = {
            'summary': event.get('summary'),
            'description': event.get('description', ''),
            'start': start_body,
            'end': end_body,
        }
        try:
            res = service.events().insert(calendarId='primary', body=body).execute()
            print(f"Event Created: {res.get('summary')} ({start_val})")
            results.append(res)
        except Exception as e:
            print(f"Event Error ({event.get('summary')}): {e}")
    return results

def test_connection(creds):
    print("\n--- TESTING CONNECTIVITY ---")
    try:
        # Test Tasks
        service_tasks = build("tasks", "v1", credentials=creds)
        results = service_tasks.tasks().list(tasklist='@default', maxResults=5).execute()
        items = results.get('items', [])
        print(f"\n[SUCCESS] Google Tasks Connected. Found {len(items)} recent tasks:")
        for item in items:
            print(f" - {item['title']} ({item.get('status')})")

        # Test Calendar
        service_cal = build("calendar", "v3", credentials=creds)
        now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
        events_result = service_cal.events().list(calendarId='primary', timeMin=now,
                                              maxResults=5, singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])
        print(f"\n[SUCCESS] Google Calendar Connected. Found {len(events)} upcoming events:")
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            print(f" - {start}: {event['summary']}")
            
        return True

    except Exception as e:
        print(f"\n[FAILURE] Connection Test Failed: {e}")
        return False

# ===== Gmail Functions =====

import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def _get_gmail_service(creds):
    """Build and return Gmail service."""
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload):
    """Extract and decode the body from a message payload."""
    body = ""

    if 'parts' in payload:
        # Multipart message - find text/plain or text/html
        for part in payload['parts']:
            mime_type = part.get('mimeType', '')
            if mime_type == 'text/plain':
                data = part.get('body', {}).get('data', '')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
                    break
            elif mime_type == 'text/html' and not body:
                data = part.get('body', {}).get('data', '')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            elif 'parts' in part:
                # Nested multipart
                body = _decode_body(part)
                if body:
                    break
    else:
        # Simple message
        data = payload.get('body', {}).get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')

    return body


def _get_header(headers, name):
    """Get a header value by name."""
    for h in headers:
        if h.get('name', '').lower() == name.lower():
            return h.get('value', '')
    return ''


def gmail_list_messages(service, query='', max_results=20, label_ids=None):
    """List messages with optional search query.

    Args:
        service: Gmail API service
        query: Gmail search query (e.g., 'is:unread', 'from:someone@example.com')
        max_results: Maximum messages to return
        label_ids: List of label IDs to filter by (e.g., ['INBOX', 'UNREAD'])

    Returns:
        List of message summaries with id, threadId, snippet, subject, from, date
    """
    try:
        results = service.users().messages().list(
            userId='me',
            q=query,
            maxResults=max_results,
            labelIds=label_ids or []
        ).execute()

        messages = results.get('messages', [])
        if not messages:
            return []

        # Get summary info for each message
        summaries = []
        for msg in messages:
            # Get metadata for each message
            msg_data = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='metadata',
                metadataHeaders=['Subject', 'From', 'Date']
            ).execute()

            headers = msg_data.get('payload', {}).get('headers', [])
            summaries.append({
                'id': msg['id'],
                'threadId': msg['threadId'],
                'snippet': msg_data.get('snippet', ''),
                'subject': _get_header(headers, 'Subject'),
                'from': _get_header(headers, 'From'),
                'date': _get_header(headers, 'Date'),
                'labelIds': msg_data.get('labelIds', [])
            })

        return summaries

    except Exception as e:
        print(f"Gmail list error: {e}")
        raise


def gmail_get_message(service, message_id, format='full'):
    """Get full message content by ID.

    Args:
        service: Gmail API service
        message_id: Message ID
        format: 'full', 'metadata', 'minimal', or 'raw'

    Returns:
        Message dict with subject, from, to, date, body, and labels
    """
    try:
        msg = service.users().messages().get(
            userId='me',
            id=message_id,
            format=format
        ).execute()

        headers = msg.get('payload', {}).get('headers', [])
        body = _decode_body(msg.get('payload', {}))

        return {
            'id': msg['id'],
            'threadId': msg['threadId'],
            'subject': _get_header(headers, 'Subject'),
            'from': _get_header(headers, 'From'),
            'to': _get_header(headers, 'To'),
            'cc': _get_header(headers, 'Cc'),
            'date': _get_header(headers, 'Date'),
            'body': body,
            'snippet': msg.get('snippet', ''),
            'labelIds': msg.get('labelIds', [])
        }

    except Exception as e:
        print(f"Gmail get error: {e}")
        raise


def gmail_send(service, to, subject, body, cc=None, bcc=None):
    """Send an email.

    Args:
        service: Gmail API service
        to: Recipient email(s) - string or list
        subject: Email subject
        body: Email body (plain text)
        cc: CC recipient(s) - optional
        bcc: BCC recipient(s) - optional

    Returns:
        Sent message info
    """
    try:
        message = MIMEMultipart()
        message['To'] = to if isinstance(to, str) else ', '.join(to)
        message['Subject'] = subject

        if cc:
            message['Cc'] = cc if isinstance(cc, str) else ', '.join(cc)
        if bcc:
            message['Bcc'] = bcc if isinstance(bcc, str) else ', '.join(bcc)

        message.attach(MIMEText(body, 'plain'))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        result = service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        print(f"Email sent: {result.get('id')}")
        return result

    except Exception as e:
        print(f"Gmail send error: {e}")
        raise


def gmail_reply(service, message_id, body, reply_all=False):
    """Reply to an existing message in the same thread.

    Args:
        service: Gmail API service
        message_id: ID of the message to reply to
        body: Reply body text
        reply_all: If True, reply to all recipients

    Returns:
        Sent message info
    """
    try:
        # Get the original message
        original = gmail_get_message(service, message_id)

        # Build recipients
        to = original['from']  # Reply to sender
        cc = None

        if reply_all:
            # Include original To and Cc (excluding self)
            original_to = original.get('to', '')
            original_cc = original.get('cc', '')
            if original_to or original_cc:
                cc = f"{original_to}, {original_cc}".strip(', ')

        # Build subject (add Re: if not present)
        subject = original['subject']
        if not subject.lower().startswith('re:'):
            subject = f"Re: {subject}"

        # Build the reply
        message = MIMEMultipart()
        message['To'] = to
        message['Subject'] = subject
        message['In-Reply-To'] = message_id
        message['References'] = message_id

        if cc:
            message['Cc'] = cc

        message.attach(MIMEText(body, 'plain'))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        result = service.users().messages().send(
            userId='me',
            body={
                'raw': raw,
                'threadId': original['threadId']  # Keep in same thread
            }
        ).execute()

        print(f"Reply sent: {result.get('id')}")
        return result

    except Exception as e:
        print(f"Gmail reply error: {e}")
        raise


def gmail_list_labels(service):
    """List all Gmail labels.

    Returns:
        List of labels with id, name, type
    """
    try:
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])

        return [{
            'id': label['id'],
            'name': label['name'],
            'type': label.get('type', 'user')
        } for label in labels]

    except Exception as e:
        print(f"Gmail labels error: {e}")
        raise


def gmail_modify_labels(service, message_ids, add_labels=None, remove_labels=None):
    """Add or remove labels from messages.

    Args:
        service: Gmail API service
        message_ids: Message ID or list of IDs
        add_labels: Label IDs to add
        remove_labels: Label IDs to remove

    Returns:
        Result dict
    """
    try:
        if isinstance(message_ids, str):
            message_ids = [message_ids]

        results = []
        for msg_id in message_ids:
            result = service.users().messages().modify(
                userId='me',
                id=msg_id,
                body={
                    'addLabelIds': add_labels or [],
                    'removeLabelIds': remove_labels or []
                }
            ).execute()
            results.append(result)
            print(f"Modified labels for message: {msg_id}")

        return {'modified': len(results), 'messages': results}

    except Exception as e:
        print(f"Gmail modify labels error: {e}")
        raise


def gmail_trash(service, message_id):
    """Move a message to trash.

    Args:
        service: Gmail API service
        message_id: Message ID to trash

    Returns:
        Trashed message info
    """
    try:
        result = service.users().messages().trash(
            userId='me',
            id=message_id
        ).execute()
        print(f"Trashed message: {message_id}")
        return result

    except Exception as e:
        print(f"Gmail trash error: {e}")
        raise


def gmail_create_draft(service, to, subject, body, cc=None, bcc=None):
    """Create a draft email for review before sending.

    Args:
        service: Gmail API service
        to: Recipient email(s)
        subject: Email subject
        body: Email body (plain text)
        cc: CC recipient(s) - optional
        bcc: BCC recipient(s) - optional

    Returns:
        Draft info with id
    """
    try:
        message = MIMEMultipart()
        message['To'] = to if isinstance(to, str) else ', '.join(to)
        message['Subject'] = subject

        if cc:
            message['Cc'] = cc if isinstance(cc, str) else ', '.join(cc)
        if bcc:
            message['Bcc'] = bcc if isinstance(bcc, str) else ', '.join(bcc)

        message.attach(MIMEText(body, 'plain'))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

        draft = service.users().drafts().create(
            userId='me',
            body={'message': {'raw': raw}}
        ).execute()

        print(f"Draft created: {draft.get('id')}")
        return {
            'id': draft['id'],
            'message_id': draft.get('message', {}).get('id')
        }

    except Exception as e:
        print(f"Gmail draft error: {e}")
        raise


def list_items(creds, limit=10):
    print(f"--- Fecthing last {limit} items ---")
    data = {"tasks": [], "events": []}
    
    try:
        # Tasks
        service_tasks = build("tasks", "v1", credentials=creds)
        results = service_tasks.tasks().list(tasklist='@default', maxResults=limit, showCompleted=False).execute()
        items = results.get('items', [])
        for item in items:
            t = {"id": item['id'], "title": item['title'], "status": item['status'], "due": item.get('due')}
            data["tasks"].append(t)
            print(f" [TASK] {t['title']} (ID: {t['id']}) - {t['due'] or 'No Due Date'}")

        # Calendar
        service_cal = build("calendar", "v3", credentials=creds)
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service_cal.events().list(calendarId='primary', timeMin=now,
                                              maxResults=limit, singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            e = {"summary": event['summary'], "start": start}
            data["events"].append(e)
            print(f" [EVENT] {start}: {e['summary']}")
            
    except Exception as e:
        print(f"Error listing items: {e}")
    
    return data

def main():
    parser = argparse.ArgumentParser(description="Google Suite Tool")
    parser.add_argument("--payload", type=str, help="JSON with 'tasks' and 'events' lists")
    parser.add_argument("--payload_file", type=str, help="Path to JSON file with payload")
    parser.add_argument("--test", action="store_true", help="Run a connection test (read-only)")
    parser.add_argument("--list", action="store_true", help="List upcoming tasks and events")
    parser.add_argument("--limit", type=int, default=10, help="Number of items to list")
    parser.add_argument("--auth", action="store_true", help="Force interactive authentication (run locally with browser)")
    args = parser.parse_args()

    try:
        creds = authenticate(headless=not args.auth)
        
        if args.test:
            test_connection(creds)
            return

        if args.list:
            list_items(creds, args.limit)
            return

        data = None
        if args.payload_file:
            with open(args.payload_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        elif args.payload:
            data = json.loads(args.payload)
        else:
            print("No payload provided. Use --batch, --payload_file, --list, or --test")
            return

        tasks = data.get('tasks', [])
        events = data.get('events', [])
        
        if tasks:
            task_service = build("tasks", "v1", credentials=creds)
            create_tasks(task_service, tasks)
            
        if events:
            cal_service = build("calendar", "v3", credentials=creds)
            create_events(cal_service, events)

        print("Done.")

    except Exception as e:
        print(f"Execution Error: {e}")

if __name__ == "__main__":
    main()
