# Second Brain User Manual

A comprehensive guide to using the Second Brain personal AI assistant system.

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Features Guide](#2-features-guide)
3. [Skills and Commands](#3-skills-and-commands)
4. [Tips and Tricks](#4-tips-and-tricks)

---

## 1. Getting Started

### What is Second Brain?

Second Brain is an AI-enhanced Personal Knowledge Management (PKM) system powered by Claude. It goes beyond a simple chatbot by integrating:

- **Persistent Memory**: Claude remembers your preferences, past conversations, and important facts across sessions
- **External Integrations**: Direct connections to Google Calendar, Tasks, Gmail, Spotify, YouTube Music, and web search
- **Autonomous Capabilities**: Scheduled tasks, background processing, and proactive assistance
- **File Management**: Read, write, and organize files in your personal workspace

Think of it as a Chief of Staff AI that manages your digital life while learning and adapting to your needs.

### How to Access the Chat Interface

1. **Start the Server**
   ```bash
   ./interface/start.sh
   ```

2. **Access the Interface**
   - **Web App**: Open http://localhost:5173 in your browser
   - **Mobile**: The interface is a Progressive Web App (PWA) - add it to your home screen for native-like experience
   - **LAN Access**: Connect from other devices on your network using your server's IP address

3. **Interface Layout**
   - **File Explorer** (left): Navigate your workspace files
   - **Editor** (center): Markdown editing with live preview
   - **Chat** (right): Conversation with Claude
   - Use the header icons to toggle each panel

### Basic Interaction Patterns

**Natural Language**: Simply type what you need. Claude understands context and intent.

```
"What's on my calendar this week?"
"Add a task to review the quarterly report by Friday"
"Search my notes for anything about the project timeline"
```

**Direct Commands**: Invoke skills with slash commands.

```
/sync           - Run the daily inbox sync
/red-team       - Stress-test an idea or plan
/research       - Research a topic internally and externally
```

**File Operations**: Ask Claude to work with files.

```
"Create a new project spec in 10_Active_Projects"
"Read the meeting notes from yesterday"
"Update the README with the new installation steps"
```

---

## 2. Features Guide

### Memory Systems

Second Brain uses a three-tier memory architecture:

#### Long-Term Memory (LTM)

**What it does**: Automatically extracts and stores facts from conversations for semantic retrieval.

**How it works**:
- A background "Librarian" agent processes conversations every ~20 minutes
- Facts are stored with embeddings for semantic search
- Memories are organized into "threads" (related fact clusters)
- Important memories (importance=100) are always included in context

**User commands**:
- Claude automatically retrieves relevant memories for each conversation
- To manually search: "Search your memory for information about [topic]"
- To add explicitly: "Remember that [important fact]"

**LTM Tools Available**:
| Tool | Description |
|------|-------------|
| `ltm_search` | Search memories semantically |
| `ltm_get_context` | Get relevant memory context for a query |
| `ltm_add_memory` | Explicitly add a fact to memory |
| `ltm_create_thread` | Organize related memories into a thread |
| `ltm_stats` | View memory system statistics |
| `ltm_process_now` | Force immediate Librarian processing |
| `ltm_run_gardener` | Run maintenance on memory store |
| `ltm_backfill` | Process historical chat history |

#### Working Memory

**What it does**: Temporary notes that persist across exchanges but auto-expire.

**Use cases**:
- Tracking context during multi-step tasks
- Reminders about ongoing work
- Temporary observations

**Features**:
- TTL (Time-To-Live): Items expire after a set number of exchanges (default: 5, max: 10)
- Pinning: Pin critical items to prevent expiration (max 3 pinned)
- Deadlines: Add deadlines with countdown warnings
- Tags: Categorize items (reminder, observation, todo, etc.)

**Working Memory Tools**:
| Tool | Description |
|------|-------------|
| `working_memory_add` | Add a temporary note |
| `working_memory_update` | Update an existing note |
| `working_memory_remove` | Remove a note |
| `working_memory_list` | List all current notes |
| `working_memory_snapshot` | Promote to permanent storage |

#### Journal (memory.md)

**What it does**: Claude's self-managed journal for reflections and observations.

**Content types**:
- Self-reflections and introspection
- Working theories and hypotheses
- Relationship notes about the user
- Lessons learned from past sessions
- Meta-observations about patterns

**Journal Tools**:
| Tool | Description |
|------|-------------|
| `memory_read` | Read the journal contents |
| `memory_append` | Add a new reflection or note |

---

### Scheduling Automated Tasks

Schedule Claude to perform tasks automatically at specified times.

**Schedule formats**:
- `every X minutes/hours` - Recurring interval
- `daily at HH:MM` - Daily at specific time
- `once at YYYY-MM-DDTHH:MM:SS` - One-time execution

**Examples**:
```
"Schedule a daily sync at 5 AM"
"Remind me to check email every 2 hours"
"Schedule a one-time reminder for 2026-02-01T09:00:00"
```

**Silent vs. Visible Tasks**:
- **Visible** (default): Appears in chat history, triggers notifications
- **Silent**: Background maintenance tasks, no notifications (use for Librarian/Gardener type tasks)

**Scheduler Tools**:
| Tool | Description |
|------|-------------|
| `schedule_self` | Schedule Primary Claude to run a prompt at a specific time |
| `schedule_agent` | Schedule a subagent to run asynchronously (outputs to 00_Inbox/agent_outputs/) |
| `scheduler_list` | List all scheduled tasks |
| `scheduler_update` | Modify an existing task |
| `scheduler_remove` | Delete a scheduled task |

---

### Google Integration (Tasks & Calendar)

Bidirectional sync with Google Tasks and Calendar.

**Tasks**:
```
"Add a task: Review budget spreadsheet by Friday"
"What tasks do I have this week?"
"Mark the dentist appointment task as complete"
"Delete the old grocery list task"
```

**Calendar Events**:
```
"Schedule a meeting with team at 3pm tomorrow"
"What's on my calendar for next Monday?"
"Block 5-6pm for deep work every weekday"
```

**Google Tools**:
| Tool | Description |
|------|-------------|
| `google_list` | List upcoming tasks and events |
| `google_create_tasks_and_events` | Create new tasks/events |
| `google_delete_task` | Delete a task by ID |
| `google_update_task` | Update task title, notes, due date, or status |

---

### Gmail Integration

Full email management from within the chat.

**Reading Email**:
```
"Show me unread emails"
"Find emails from john@example.com"
"Get the full content of that last email"
```

**Sending Email**:
```
"Send an email to team@company.com about the project update"
"Draft a reply to that message (don't send yet)"
"Reply to that email thread"
```

**Organizing**:
```
"Archive those three emails"
"Mark that message as read"
"Star the email from HR"
"Move that to trash"
```

**Gmail Tools**:
| Tool | Description |
|------|-------------|
| `gmail_list_messages` | Search and list emails |
| `gmail_get_message` | Get full message content |
| `gmail_send` | Send an email immediately |
| `gmail_reply` | Reply to an existing thread |
| `gmail_draft_create` | Create a draft for review |
| `gmail_list_labels` | List all labels |
| `gmail_modify_labels` | Add/remove labels from messages |
| `gmail_trash` | Move message to trash |

---

### Music Integrations

#### Spotify

Control playback, manage playlists, and discover music.

**Playback** (requires Premium):
```
"What's currently playing on Spotify?"
"Pause the music"
"Skip to next track"
```

**Discovery**:
```
"What have I been listening to recently?"
"Show my top artists from the last month"
"Search Spotify for 'ambient electronic'"
```

**Playlists**:
```
"Show my Spotify playlists"
"Create a playlist called 'Focus Music'"
"Add that song to my workout playlist"
```

**Spotify Tools**:
| Tool | Description |
|------|-------------|
| `spotify_now_playing` | Get current track |
| `spotify_playback_control` | Play/pause/skip |
| `spotify_recently_played` | Recent listening history |
| `spotify_top_items` | Top artists/tracks by time range |
| `spotify_search` | Search tracks, artists, albums |
| `spotify_get_playlists` | List user playlists |
| `spotify_create_playlist` | Create new playlist |
| `spotify_add_to_playlist` | Add tracks to playlist |

Note: First-time setup requires OAuth authentication via `spotify_auth_start` and `spotify_auth_callback`.

#### YouTube Music

Manage playlists and liked songs.

**Playlists**:
```
"Show my YouTube Music playlists"
"Create a playlist called 'Road Trip'"
"Add this song to my favorites"
```

**YouTube Music Tools**:
| Tool | Description |
|------|-------------|
| `ytmusic_get_playlists` | List playlists |
| `ytmusic_get_playlist_items` | Get songs in a playlist |
| `ytmusic_get_liked` | Get liked songs |
| `ytmusic_search` | Search for music |
| `ytmusic_create_playlist` | Create new playlist |
| `ytmusic_add_to_playlist` | Add song to playlist |
| `ytmusic_remove_from_playlist` | Remove song from playlist |
| `ytmusic_delete_playlist` | Delete a playlist (permanent) |

---

### Web Research Capabilities

Claude can search the web and fetch/parse web pages.

#### Web Search

Powered by Perplexity API for current information.

```
"Search the web for latest Python 3.12 features"
"Find recent news about AI regulations"
"What's the current weather in Austin?"
```

**Filtering options**:
- Recency: day, week, month, year
- Country: ISO 2-letter code
- Domains: Include/exclude specific sites

#### Page Parser

Fetch and parse web pages into clean Markdown.

```
"Fetch and summarize this article: [URL]"
"Parse these documentation pages: [URLs]"
```

**Features**:
- Extracts main content (removes ads, navigation)
- Supports HTML and PDF
- Multiple URLs in one call
- Auto-saves truncated content for later retrieval

**Web Tools**:
| Tool | Description |
|------|-------------|
| `web_search` | Search the web via Perplexity |
| `page_parser` | Fetch and parse URLs to Markdown |

---

### Notifications

Claude can notify you through multiple channels.

**Automatic Notifications**:
- When scheduled tasks complete
- When important context requires attention

**Critical Notifications**:
For urgent situations, Claude can escalate via:
- In-app toast notification
- Push notification
- Email to your Gmail

```
"Send me a critical notification about the deadline"
```

---

### Server Management

Claude can restart the server to apply changes.

**Quick Restart**: Server only (~5 seconds)
**Full Restart**: Includes frontend rebuild (~30 seconds)

Conversations automatically continue after restart.

---

## 3. Skills and Commands

Skills are specialized workflows invoked via slash commands or by asking Claude to use them.

### Available Skills

#### `/sync` - Intelligent Agentic Sync

**Purpose**: Process inbox, organize knowledge, sync external services, schedule deep work.

**What it does**:
1. **Ingest**: Scans `00_Inbox/` for all files
2. **Process**: Routes information:
   - Tasks → Google Tasks
   - Events → Google Calendar
   - Journal entries → Daily Notes
   - Ideas → Project Files
3. **Velocity Defense**: Schedules a daily "Deep Work" block
4. **Cleanup**: Archives processed files, resets scratchpad

**Usage**: "Run /sync" or schedule it daily at 5 AM

---

#### `/red-team` - Stress Test Ideas

**Purpose**: Identify weaknesses in plans, ideas, or architectures.

**What it does**:
1. Security and logic gap analysis
2. Scalability assessment
3. Devil's advocate critique (time, money, complexity risks)
4. Proposes specific mitigations

**Usage**: "Red-team this migration plan" or "/red-team [describe your idea]"

---

#### `/research-assistant` - Research Topics

**Purpose**: Deepen understanding with internal and external research.

**What it does**:
1. Internal search across your workspace
2. External web search for related resources, libraries, news
3. Synthesis into a "Context & Resources" section

**Usage**: "/research [topic]" or "Research the best approaches to [problem]"

---

#### `/expand-and-structure` - Structure Brain Dumps

**Purpose**: Convert raw notes into formal project specifications.

**What it does**:
1. Analyzes raw text for objectives, constraints, unknowns
2. Creates structured document:
   - Executive Summary
   - The "Why" (connection to goals)
   - Implementation Plan
   - Open Questions
3. Preserves original text in "Raw Notes" section

**Usage**: "/expand-and-structure [paste your notes]"

---

#### `/scaffold-mvp` - Generate Project Structure

**Purpose**: Create actual file/folder structures for technical projects.

**What it does**:
1. Analyzes project specification
2. Creates directory structure and starter files
3. Reports what was created (tree view)
4. Suggests next steps

**Usage**: "/scaffold-mvp [describe the project structure]"

---

#### `/finance` - Financial Data Access

**Purpose**: Access banking information via Plaid integration.

**What it does**:
- List connected accounts and balances
- View recent transactions
- Spending analysis by category

**Prerequisites**: Plaid API keys configured

---

## 4. Tips and Tricks

### How to Get the Best Results

1. **Be Specific About Intent**
   - Instead of "help with email", say "draft a polite decline to the meeting invite from Sarah"

2. **Provide Context**
   - Claude has memory but appreciates reminders: "Continuing from our discussion about the API refactor..."

3. **Use the Right Tool for the Job**
   - Quick tasks → direct request
   - Complex multi-step work → use skills
   - Background processing → schedule it

4. **Trust the Memory System**
   - You don't need to repeat yourself - Claude remembers
   - Important facts are automatically preserved
   - Ask "What do you remember about X?" to verify

### What Claude Can Do Autonomously

Based on established permissions:

**Full Autonomy**:
- Read/write any file in the workspace
- Schedule and execute automated tasks
- Process and organize your inbox
- Manage Google Tasks and Calendar
- Search your notes and the web
- Play/control music

**Requires Approval**:
- Spending money
- Public-facing actions (posting, publishing)

**Claude Time (1-7 AM)**:
- Silent burst execution while you sleep
- Chains through entire project phases overnight
- Non-urgent work gets queued here

**Zeke Time (7 AM - 1 AM)**:
- Active collaboration mode
- Notifications and escalations allowed

### When to Use Which Features

| Need | Use This |
|------|----------|
| Quick question | Direct chat |
| Process inbox | `/sync` |
| Critique a plan | `/red-team` |
| Research a topic | `/research-assistant` |
| Structure raw notes | `/expand-and-structure` |
| Create project files | `/scaffold-mvp` |
| Remember something important | "Remember that..." |
| Track temporary context | Working memory |
| Recurring automation | Scheduler |
| Urgent notification | Critical notification tool |

### Workspace Organization

The Second Brain follows a numbered folder structure:

```
00_Inbox/         - Entry point for raw capture
10_Active_Projects/ - Current focus areas
20_Areas/         - Long-term responsibilities
30_Incubator/     - Ideas in development
.99_Archive/      - Archived/completed items
```

**Inbox Workflow**:
1. Dump thoughts into `00_Inbox/scratchpad.md` or create new files
2. Run `/sync` (or let the scheduled sync handle it)
3. Claude routes content to appropriate locations
4. Processed files move to archive

### Pro Tips

1. **Morning Routine**: Start with "What's on my agenda today?" for a quick briefing

2. **End of Day**: "Summarize what we accomplished and what's pending" to capture state

3. **Context Handoff**: If you're about to be AFK, tell Claude what to work on during Claude Time

4. **Memory Debugging**: Use `ltm_stats` to check memory health, `ltm_process_now` to force processing

5. **Draft First**: For important emails, use `gmail_draft_create` to review before sending

6. **Playlist Management**: Build playlists conversationally - "Create a chill evening playlist and add songs similar to what I've been listening to"

7. **Research Mode**: Combine web search with page parser - search for articles, then fetch and summarize the best ones

8. **Working Memory for Tracking**: Pin critical context (like "currently debugging auth issue") so it persists across the session

---

## Appendix: Quick Reference

### All Available Tools

**Google**:
`google_list`, `google_create_tasks_and_events`, `google_delete_task`, `google_update_task`

**Gmail**:
`gmail_list_messages`, `gmail_get_message`, `gmail_send`, `gmail_reply`, `gmail_draft_create`, `gmail_list_labels`, `gmail_modify_labels`, `gmail_trash`

**Scheduler**:
`schedule_self`, `schedule_agent`, `scheduler_list`, `scheduler_update`, `scheduler_remove`

**Memory**:
`memory_read`, `memory_append`, `working_memory_add`, `working_memory_update`, `working_memory_remove`, `working_memory_list`, `working_memory_snapshot`, `ltm_search`, `ltm_get_context`, `ltm_add_memory`, `ltm_create_thread`, `ltm_stats`, `ltm_process_now`, `ltm_run_gardener`, `ltm_buffer_exchange`, `ltm_backfill`

**Spotify**:
`spotify_auth_start`, `spotify_auth_callback`, `spotify_now_playing`, `spotify_playback_control`, `spotify_recently_played`, `spotify_top_items`, `spotify_search`, `spotify_get_playlists`, `spotify_create_playlist`, `spotify_add_to_playlist`

**YouTube Music**:
`ytmusic_get_playlists`, `ytmusic_get_playlist_items`, `ytmusic_get_liked`, `ytmusic_search`, `ytmusic_create_playlist`, `ytmusic_add_to_playlist`, `ytmusic_remove_from_playlist`, `ytmusic_delete_playlist`

**Utilities**:
`web_search`, `page_parser`, `send_critical_notification`, `restart_server`

### Skills
`/sync`, `/red-team`, `/research-assistant`, `/expand-and-structure`, `/scaffold-mvp`, `/finance`

---

*Last updated: 2026-01-29*
