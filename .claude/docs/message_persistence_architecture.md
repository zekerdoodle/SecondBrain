# Message Persistence Architecture

This document describes the robust message persistence system that ensures messages are never lost, even during network failures, server restarts, or app crashes.

## Overview

The system implements a **Write-Ahead Log (WAL)** pattern combined with **optimistic UI** and **server-side recovery**. This ensures:

1. User messages are never lost
2. Partial Claude responses can be recovered
3. Connection interruptions are handled gracefully
4. Mobile PWA and desktop work identically

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Frontend (React)                          │
│                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐             │
│  │  Message    │    │  WebSocket  │    │   Session   │             │
│  │  Queue      │───>│  Handler    │───>│   Storage   │             │
│  │  (pending)  │    │             │    │             │             │
│  └─────────────┘    └─────────────┘    └─────────────┘             │
│        │                   │                   │                    │
│        │ status: pending   │                   │ PENDING_MSG_KEY    │
│        │ status: confirmed │                   │ LAST_SYNC_KEY      │
│        │ status: complete  │                   │                    │
└────────┼───────────────────┼───────────────────┼────────────────────┘
         │                   │                   │
         │              WebSocket               HTTP
         │                   │                   │
┌────────┼───────────────────┼───────────────────┼────────────────────┐
│        │                   │                   │                    │
│        ▼                   ▼                   ▼                    │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐             │
│  │   WAL       │    │   Message   │    │   Sync      │             │
│  │  Writer     │<───│  Handler    │<───│   API       │             │
│  │             │    │             │    │             │             │
│  └─────────────┘    └─────────────┘    └─────────────┘             │
│        │                   │                   │                    │
│        ▼                   ▼                   │                    │
│  ┌─────────────┐    ┌─────────────┐           │                    │
│  │ pending_    │    │ streaming_  │           │                    │
│  │ messages.   │    │ responses.  │           │                    │
│  │ json        │    │ json        │           │                    │
│  └─────────────┘    └─────────────┘           │                    │
│        │                   │                   │                    │
│        └───────────────────┼───────────────────┘                    │
│                            ▼                                        │
│                     ┌─────────────┐                                 │
│                     │   Chat      │                                 │
│                     │   Files     │                                 │
│                     │ (.json)     │                                 │
│                     └─────────────┘                                 │
│                                                                     │
│                           Backend (Python/FastAPI)                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Message Lifecycle

### 1. Message Sending

```
User types message
       │
       ▼
┌──────────────────┐
│ Add to UI with   │
│ status: pending  │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Save to          │
│ sessionStorage   │
│ (PENDING_MSG_KEY)│
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Send via         │
│ WebSocket        │
└────────┬─────────┘
         │
         ▼ (Backend receives)
┌──────────────────┐
│ Write to WAL     │
│ BEFORE processing│
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Send ACK to      │
│ frontend         │
└────────┬─────────┘
         │
         ▼ (Frontend receives ACK)
┌──────────────────┐
│ Update UI:       │
│ status: confirmed│
└──────────────────┘
```

### 2. Response Streaming

```
Claude generates response
         │
         ▼
┌──────────────────┐
│ Stream to        │
│ frontend         │
└────────┬─────────┘
         │
         │ (Every 5 seconds or on tool use)
         ▼
┌──────────────────┐
│ Checkpoint to    │
│ streaming_       │
│ responses.json   │
└────────┬─────────┘
         │
         ▼ (On completion)
┌──────────────────┐
│ Save to chat     │
│ file             │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Clear WAL        │
│ entries          │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Send 'done'      │
│ event            │
└────────┬─────────┘
         │
         ▼ (Frontend receives)
┌──────────────────┐
│ Update UI:       │
│ status: complete │
└──────────────────┘
```

## Failure Recovery

### Case 1: Network Disconnect During Send

```
Message sent
     │
     ▼
Network dies (no ACK received)
     │
     ▼ (After 5 seconds)
Frontend shows: status: failed
     │
     ▼
Frontend calls syncWithServer()
     │
     ▼
If message in WAL: show as confirmed
If in chat file: show full state
```

### Case 2: Server Restart Mid-Response

```
Server crashes
     │
     ▼ (On restart)
┌─────────────────────┐
│ Check WAL for       │
│ recovery_state      │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ For each streaming  │
│ response: save      │
│ partial content     │
│ to chat file with   │
│ "[recovered]" note  │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ Client reconnects,  │
│ syncs from server   │
└─────────────────────┘
```

### Case 3: App Close During Processing

```
User closes app/switches tabs
     │
     ▼ (On app reopen)
┌─────────────────────┐
│ Check sessionStorage│
│ for PENDING_MSG_KEY │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ Call /api/chat/     │
│ pending/{session}   │
└────────┬────────────┘
         │
         ├── Has pending: show status
         │
         └── No pending: sync full state
```

## Key Files

### Frontend

- `interface/client/src/types.ts` - `MessageStatus` type and `ChatMessage` interface
- `interface/client/src/useClaude.ts` - WebSocket handling, message queue, sync logic
- `interface/client/src/Chat.tsx` - Status indicators (checkmarks, etc.)

### Backend

- `interface/server/message_wal.py` - Write-Ahead Log implementation
- `interface/server/main.py` - Message handling, sync endpoints, recovery on startup

### WAL Directory

Location: `.claude/wal/`

Files:
- `pending_messages.json` - User messages awaiting completion
- `streaming_responses.json` - In-progress Claude responses

## API Endpoints

### POST /api/chat/sync

Sync client state with server after reconnection.

Request:
```json
{
  "session_id": "abc-123",
  "last_message_id": "1706000000000"
}
```

Response:
```json
{
  "status": "ok",
  "session_id": "abc-123",
  "messages": [...],
  "has_pending": false
}
```

### GET /api/chat/pending/{session_id}

Check if there's a pending message for a session.

Response:
```json
{
  "has_pending": true,
  "msg_id": "1706000000000",
  "status": "processing",
  "timestamp": 1706000000.0,
  "ack_sent": true
}
```

## Message Status States

| Status | Icon | Meaning |
|--------|------|---------|
| `pending` | Clock | Sent to WebSocket, awaiting server ACK |
| `confirmed` | Single check | Server received and wrote to WAL |
| `processing` | (shown in status bar) | Claude is generating response |
| `complete` | Double check | Response received and saved |
| `failed` | Alert | No ACK after timeout, may need resync |

## Configuration

### Checkpoint Interval

Streaming responses are checkpointed every 5 seconds (configurable in `message_wal.py`):

```python
CHECKPOINT_INTERVAL = 5.0  # seconds
```

### ACK Timeout

Frontend warns about potential loss after 5 seconds (configurable in `useClaude.ts`):

```typescript
const ackTimeoutMs = 5000;  // 5 seconds
```

### WAL Cleanup

Old WAL entries (>24 hours) are cleaned up on server startup:

```python
wal.clear_old_entries(max_age_hours=24)
```

## Testing Failure Modes

1. **Network disconnect**: Use browser DevTools to go offline mid-response
2. **Server restart**: Stop/start the server during streaming
3. **App close**: Close tab during response, reopen
4. **Message edit**: Edit a message and verify state consistency

## Limitations

- WAL is stored on local disk, not replicated
- Recovery adds "[recovered]" note to partial responses
- Extended network outages may show stale pending status

## Future Improvements

- [ ] Retry failed messages automatically
- [ ] Show detailed recovery status to user
- [ ] Add client-side IndexedDB backup
- [ ] Implement server-sent events as WebSocket fallback
