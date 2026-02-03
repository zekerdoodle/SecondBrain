# Notification System Specification

## Status: IMPLEMENTED (2026-01-28)

This document reflects the **current implementation** after initial development.

---

## Overview

Claude can notify Zeke of new messages via platform-appropriate notifications, with email as a critical fallback.

## Core Principle

**Notify when there's a new message in a chat Zeke is not actively viewing, and the chat is not silent.**

---

## Architecture

### Files Created

| File | Purpose |
|------|---------|
| `interface/client/src/hooks/useVisibility.ts` | Page Visibility API + focus tracking |
| `interface/client/src/hooks/useTabFlash.ts` | Tab title flashing ("Second Brain" ↔ "(1) New message") |
| `interface/client/src/hooks/usePushSubscription.ts` | Push subscription management hook (not actively used - subscription happens in Toast.tsx) |
| `interface/client/src/sw.ts` | Custom service worker for push notifications |
| `interface/client/public/sounds/notification.mp3` | Notification chime (generated two-tone C5→G5) |
| `interface/server/notifications.py` | Notification decision logic (`should_notify()`, `send_notification()`) |
| `interface/server/push_service.py` | VAPID-based Web Push sending |
| `.claude/secrets/vapid_keys.json` | VAPID keypair for push authentication |
| `.claude/push_subscriptions.json` | Stored push subscriptions (created on first subscribe) |

### Files Modified

| File | Changes |
|------|---------|
| `interface/client/src/useClaude.ts` | Sends `visibility_update` WebSocket messages, handles `new_message_notification` events, 60s heartbeat |
| `interface/client/src/Toast.tsx` | Added `notification` toast type, sound playback, auto push subscription on permission grant |
| `interface/client/src/Chat.tsx` | `handleNewMessageNotification` shows toast with sound/flash; `handleScheduledTaskComplete` only refreshes history |
| `interface/client/src/main.tsx` | Preloads notification sound on first click |
| `interface/client/vite.config.ts` | Uses `injectManifest` strategy for custom service worker |
| `interface/server/main.py` | `ClientSession` dataclass tracks visibility per WebSocket, handles `visibility_update` action, push subscription REST endpoints, notification decision integration |
| `interface/server/mcp_tools.py` | Added `send_critical_notification` MCP tool |

---

## Trigger Conditions

A notification fires when ALL of these are true:

1. **New message exists** - Claude (or scheduled task) posted a message
2. **Chat is not silent** - The session does not have `is_silent=True`
3. **User is not actively viewing** - No connected client has that chat focused

### Visibility Detection

**Frontend (`useVisibility.ts`):**
- Combines `document.visibilityState === 'visible'` AND `document.hasFocus()`
- Sends `visibility_update` WebSocket message on state change + every 60 seconds (heartbeat)

**Backend (`main.py`):**
- `ClientSession` dataclass per WebSocket connection tracks: `is_active`, `current_chat_id`, `last_heartbeat`
- Connection considered stale if no heartbeat in 90 seconds

### Silent Flag

Silent chats (background automation) never trigger notifications. The `silent` flag from scheduler flows to `is_system` in saved chat data.

---

## Notification Channels

### Decision Logic (`notifications.py`)

```python
def should_notify(chat_id, is_silent, client_sessions, critical=False) -> NotificationDecision:
    # Returns: notify, use_toast, use_push, use_email, play_sound, reason
```

| Scenario | Toast | Sound | Tab Flash | Push | Email |
|----------|-------|-------|-----------|------|-------|
| User viewing that chat | No | No | No | No | No |
| User online, not viewing | Yes | Yes | Yes | No | No |
| User offline (no connections) | No | No | No | Yes | No |
| Critical message | Yes | Yes | Yes | Yes | Yes |

### Desktop Behavior

When notification triggers for online user:
1. **Toast** - Purple notification toast (bottom-right), 8s auto-dismiss
2. **Sound** - Plays `/sounds/notification.mp3` (two-tone chime)
3. **Tab Flash** - Title alternates "Second Brain" ↔ "(1) New message" every 1s until focus

### Mobile/Offline Behavior (Push)

When user has no active connections:
1. **Push notification** via Web Push API
2. Service worker (`sw.ts`) shows system notification
3. Click opens app and posts message to navigate to chat

### Email Fallback (Critical Only)

Triggered ONLY by explicit MCP tool call:
```
send_critical_notification(message="...", context="...")
```

- Sends to authenticated Gmail account (from OAuth)
- Subject: "URGENT: Claude needs your attention"
- Also triggers all other channels

---

## WebSocket Protocol

### Client → Server

```typescript
// Visibility update (on change + every 60s heartbeat)
{ action: 'visibility_update', isActive: boolean, chatId?: string }
```

### Server → Client

```typescript
// New message notification
{
  type: 'new_message_notification',
  chatId: string,
  preview: string,      // First ~200 chars
  critical: boolean,
  playSound: boolean
}

// Scheduled task complete (for history refresh, no toast)
{
  type: 'scheduled_task_complete',
  session_id: string,
  title: string
}
```

---

## Push Subscription Flow

1. User clicks anywhere in app (first interaction)
2. `requestNotificationPermission()` in Toast.tsx runs
3. If permission granted, `subscribeToPush()` auto-runs
4. Fetches VAPID public key from `GET /api/push/vapid-public-key`
5. Creates push subscription via `pushManager.subscribe()`
6. Sends subscription to `POST /api/push/subscribe`
7. Server stores in `.claude/push_subscriptions.json`

### REST Endpoints

- `GET /api/push/vapid-public-key` - Returns public key for subscription
- `POST /api/push/subscribe` - Register subscription `{endpoint, keys: {p256dh, auth}}`
- `POST /api/push/unsubscribe` - Remove subscription

---

## MCP Tool: send_critical_notification

```python
@tool(name="send_critical_notification")
async def send_critical_notification(args):
    # message: required - The urgent message
    # context: optional - Why this is urgent

    # 1. Gets user email from OAuth profile
    # 2. Sends email via gmail_send()
    # 3. Triggers push notification
    # Returns confirmation
```

**When Claude should use this:**
- Genuinely time-sensitive (deadline within hours)
- Requires Zeke's input to proceed
- Missing it would have meaningful negative consequences

---

## Known Issues / TODO

1. **Visibility logging** - Currently logs at INFO level; may want to reduce to DEBUG after confirming it works
2. **Push on iOS** - Web Push has limited iOS support; may need native app for reliable mobile notifications
3. **Notification grouping** - If multiple notifications queue while away, they show individually (spec suggested collapsing to "3 new messages")
4. **Sound volume** - Hardcoded at 0.5; could add localStorage preference
5. **Permission UX** - Currently requests on first click; could add soft prompt banner first

---

## Testing Commands

**Schedule non-silent task:**
```
Schedule a non-silent task in 1 minute that says "Test notification"
```

**Schedule silent task (should NOT notify):**
```
Schedule a silent task in 1 minute that says "Silent test"
```

**Test critical notification:**
```
Use send_critical_notification with message "Test critical" and context "Testing"
```

**Check push subscriptions:**
```bash
cat /home/debian/second_brain/.claude/push_subscriptions.json
```

**Watch server logs:**
```bash
tail -f /home/debian/second_brain/interface/server/server.log
```

---

## Dependencies

- `pywebpush` - Python library for sending Web Push notifications
- `py-vapid` - VAPID key generation
- `workbox-precaching`, `workbox-core` - Service worker caching (via vite-plugin-pwa)

---

*Last updated: 2026-01-28*
