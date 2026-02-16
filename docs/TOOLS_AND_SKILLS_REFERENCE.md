# Tools & Skills Reference

Complete inventory of all tools and skills available in the Second Brain system.

---

# = TOOLS =

## Agent SDK Tools

### Task
**DESCRIPTION:** Launch specialized subagents to handle complex, multi-step tasks autonomously. Orchestrates work by delegating to specialized agents that have their own context windows.
**INPUTS:**
- `prompt` (string, required): The task for the agent to perform
- `description` (string, required): Short 3-5 word description
- `subagent_type` (string, required): Agent type - "Bash", "general-purpose", "Explore", "Plan", "statusline-setup"
- `model` (string, optional): "sonnet", "opus", or "haiku"
- `run_in_background` (boolean, optional): Run async, returns output_file path
- `resume` (string, optional): Agent ID to resume from
- `max_turns` (integer, optional): Max agentic turns before stopping
**OUTPUTS:** Agent response with results and agent_id for potential resume

---

### TaskOutput
**DESCRIPTION:** Retrieves output from a running or completed task (background shell, agent, or remote session).
**INPUTS:**
- `task_id` (string, required): The task ID to get output from
- `block` (boolean, default: true): Whether to wait for completion
- `timeout` (number, default: 30000): Max wait time in ms (max 600000)
**OUTPUTS:** Task output along with status information

---

### Bash
**DESCRIPTION:** Execute bash commands with optional timeout. For terminal operations like git, npm, docker. Working directory persists between commands.
**INPUTS:**
- `command` (string, required): The command to execute
- `description` (string, optional): Clear description of what command does
- `timeout` (number, optional): Timeout in ms (max 600000, default 120000)
- `run_in_background` (boolean, optional): Run in background
- `dangerouslyDisableSandbox` (boolean, optional): Override sandbox mode
**OUTPUTS:** Command stdout/stderr, truncated at 30000 chars if exceeded

---

### Glob
**DESCRIPTION:** Fast file pattern matching. Supports glob patterns like "**/*.js". Returns matching file paths sorted by modification time.
**INPUTS:**
- `pattern` (string, required): Glob pattern to match files against
- `path` (string, optional): Directory to search in (default: cwd)
**OUTPUTS:** List of matching file paths

---

### Grep
**DESCRIPTION:** Powerful search tool built on ripgrep. Supports full regex syntax.
**INPUTS:**
- `pattern` (string, required): Regex pattern to search for
- `path` (string, optional): File or directory to search in
- `glob` (string, optional): Glob pattern to filter files (e.g., "*.js")
- `type` (string, optional): File type (e.g., "js", "py", "rust")
- `output_mode` (string, optional): "content", "files_with_matches" (default), or "count"
- `-A`, `-B`, `-C` (number, optional): Context lines after/before/around match
- `-i` (boolean, optional): Case insensitive
- `-n` (boolean, optional): Show line numbers (default: true)
- `multiline` (boolean, optional): Enable multiline mode
- `head_limit`, `offset` (number, optional): Limit/offset output
**OUTPUTS:** Matching lines, file paths, or counts depending on mode

---

### Read
**DESCRIPTION:** Read files from local filesystem. Supports text, images, PDFs, Jupyter notebooks.
**INPUTS:**
- `file_path` (string, required): Absolute path to file
- `offset` (number, optional): Line number to start from
- `limit` (number, optional): Number of lines to read
**OUTPUTS:** File contents with line numbers (cat -n format), up to 2000 lines by default

---

### Edit
**DESCRIPTION:** Exact string replacements in files. Requires reading file first.
**INPUTS:**
- `file_path` (string, required): Absolute path to file
- `old_string` (string, required): Text to replace (must be unique in file)
- `new_string` (string, required): Replacement text
- `replace_all` (boolean, default: false): Replace all occurrences
**OUTPUTS:** Confirmation of edit success

---

### Write
**DESCRIPTION:** Write file to local filesystem. Overwrites existing files (requires Read first for existing files).
**INPUTS:**
- `file_path` (string, required): Absolute path to file
- `content` (string, required): Content to write
**OUTPUTS:** Confirmation of write success

---

### NotebookEdit
**DESCRIPTION:** Replace, insert, or delete cells in Jupyter notebooks (.ipynb).
**INPUTS:**
- `notebook_path` (string, required): Absolute path to notebook
- `new_source` (string, required): New cell source
- `cell_id` (string, optional): ID of cell to edit
- `cell_type` (string, optional): "code" or "markdown"
- `edit_mode` (string, optional): "replace" (default), "insert", or "delete"
**OUTPUTS:** Confirmation of edit success

---

### WebFetch
**DESCRIPTION:** Fetch content from URL, convert HTML to markdown, process with AI model.
**INPUTS:**
- `url` (string, required): Fully-formed URL to fetch
- `prompt` (string, required): What information to extract
**OUTPUTS:** Model's analysis of page content (may be summarized if large)

---

### TodoWrite
**DESCRIPTION:** Create and manage structured task list for tracking complex multi-step work.
**INPUTS:**
- `todos` (array, required): List of todo objects with:
  - `content` (string): Imperative task description
  - `activeForm` (string): Present continuous form
  - `status` (string): "pending", "in_progress", or "completed"
**OUTPUTS:** Updated todo list displayed to user

---

### KillShell
**DESCRIPTION:** Kill a running background bash shell by ID.
**INPUTS:**
- `shell_id` (string, required): ID of background shell to kill
**OUTPUTS:** Success or failure status

---

### AskUserQuestion
**DESCRIPTION:** Ask user questions to gather preferences, clarify instructions, or get decisions.
**INPUTS:**
- `questions` (array, 1-4 items, required): Questions with:
  - `question` (string): Complete question
  - `header` (string): Short label (max 12 chars)
  - `options` (array, 2-4): Each with `label` and `description`
  - `multiSelect` (boolean): Allow multiple selections
- `metadata` (object, optional): Tracking metadata
**OUTPUTS:** User's selected answers

---

### Skill
**DESCRIPTION:** Execute a skill (workflow template) within the conversation.
**INPUTS:**
- `skill` (string, required): Skill name (e.g., "commit", "sync")
- `args` (string, optional): Arguments for the skill
**OUTPUTS:** Skill execution results

---

### EnterPlanMode
**DESCRIPTION:** Transition to plan mode for designing implementation approach before coding.
**INPUTS:** None
**OUTPUTS:** Enters planning state for user approval before implementation

---

### ExitPlanMode
**DESCRIPTION:** Signal plan is complete and ready for user approval.
**INPUTS:**
- `allowedPrompts` (array, optional): Prompt-based permissions needed
- `pushToRemote` (boolean, optional): Push to remote Claude.ai session
- `remoteSessionId/Title/Url` (strings, optional): Remote session details
**OUTPUTS:** User review of plan file

---

## MCP Brain Tools

### Memory Tools

#### mcp__brain__memory_read
**DESCRIPTION:** Read Claude's self-managed journal (memory.md). Contains reflections and observations distinct from LTM.
**INPUTS:** None
**OUTPUTS:** Contents of memory.md journal

---

#### mcp__brain__memory_append
**DESCRIPTION:** Append to Claude's self-managed journal. For self-reflections, opinions, working theories, blockers, meta-observations.
**INPUTS:**
- `content` (string, required): Reflection or note to record
- `section` (string, required): Section name (e.g., "Self-Reflections", "Working Theories", "Lessons Learned")
**OUTPUTS:** Confirmation of append

---

### Working Memory Tools

#### mcp__brain__working_memory_list
**DESCRIPTION:** List all current working memory items with their status.
**INPUTS:** None
**OUTPUTS:** List of ephemeral notes with TTL, tags, pinned status, deadlines

---

#### mcp__brain__working_memory_add
**DESCRIPTION:** Add ephemeral note to working memory. Auto-expires based on TTL (exchanges).
**INPUTS:**
- `content` (string, required): Note content
- `ttl` (integer, optional): Time-to-live in exchanges (default: 5, max: 10)
- `tag` (string, optional): Category tag
- `pinned` (boolean, optional): Never auto-expire (max 3 pinned)
- `deadline` (string, optional): ISO timestamp deadline
- `remind_before` (string, optional): Warning time (e.g., "2h", "24h")
**OUTPUTS:** Confirmation of add

---

#### mcp__brain__working_memory_update
**DESCRIPTION:** Update existing working memory item by display index.
**INPUTS:**
- `index` (integer, required): Item number (1-based)
- `content` (string, optional): New content
- `append` (string, optional): Text to append
- `ttl` (integer, optional): Reset TTL
- `tag` (string, optional): New tag
- `pinned` (boolean, optional): Set pinned status
- `deadline` (string, optional): New deadline
- `remind_before` (string, optional): Warning time
**OUTPUTS:** Confirmation of update

---

#### mcp__brain__working_memory_remove
**DESCRIPTION:** Remove working memory item by display index.
**INPUTS:**
- `index` (integer, required): Item number (1-based)
**OUTPUTS:** Confirmation of removal

---

#### mcp__brain__working_memory_snapshot
**DESCRIPTION:** Promote working memory item to permanent storage (memory.md).
**INPUTS:**
- `index` (integer, required): Item number (1-based)
- `section` (string, optional): Target section (default: "Promoted from Working Memory")
- `note` (string, optional): Note to append when saving
- `keep` (boolean, default: false): Keep in working memory after promotion
**OUTPUTS:** Confirmation of promotion

---

### Google Tools

#### mcp__brain__google_list
**DESCRIPTION:** List upcoming Google Tasks and Calendar events.
**INPUTS:**
- `limit` (integer, optional): Max items to return (default: 10)
**OUTPUTS:** List of tasks and events

---

#### mcp__brain__google_create_tasks_and_events
**DESCRIPTION:** Create Google Tasks and/or Calendar events.
**INPUTS:**
- `tasks` (array, optional): Task objects with:
  - `title` (string, required)
  - `notes` (string, optional)
  - `due` (string, optional): YYYY-MM-DD or ISO datetime
- `events` (array, optional): Event objects with:
  - `summary` (string, required)
  - `description` (string, optional)
  - `start` (string, required): YYYY-MM-DD or ISO datetime
  - `end` (string, required): YYYY-MM-DD or ISO datetime
**OUTPUTS:** Created task/event IDs

---

#### mcp__brain__google_update_task
**DESCRIPTION:** Update a Google Task's fields.
**INPUTS:**
- `task_id` (string, required): Task ID from google_list
- `title` (string, optional)
- `notes` (string, optional)
- `due` (string, optional): YYYY-MM-DD or ISO
- `status` (string, optional): "needsAction" or "completed"
**OUTPUTS:** Confirmation of update

---

#### mcp__brain__google_delete_task
**DESCRIPTION:** Delete a Google Task by ID.
**INPUTS:**
- `task_id` (string, required): Task ID to delete
**OUTPUTS:** Confirmation of deletion

---

### Gmail Tools

#### mcp__brain__gmail_list_messages
**DESCRIPTION:** List and search Gmail messages. Supports Gmail search syntax.
**INPUTS:**
- `query` (string, optional): Gmail search query (e.g., "is:unread from:someone@example.com")
- `label_ids` (array, optional): Filter by labels (e.g., ["INBOX", "UNREAD"])
- `max_results` (integer, optional): Max messages (default: 20)
**OUTPUTS:** Message summaries with id, subject, from, date, snippet, labels

---

#### mcp__brain__gmail_get_message
**DESCRIPTION:** Get full content of Gmail message by ID.
**INPUTS:**
- `message_id` (string, required): Message ID from gmail_list_messages
**OUTPUTS:** Complete message with subject, from, to, cc, date, body, labels

---

#### mcp__brain__gmail_send
**DESCRIPTION:** Compose and send email immediately.
**INPUTS:**
- `to` (string, required): Recipient(s), comma-separated
- `subject` (string, required): Email subject
- `body` (string, required): Email body (plain text)
- `cc` (string, optional): CC recipients
- `bcc` (string, optional): BCC recipients
**OUTPUTS:** Sent message ID

---

#### mcp__brain__gmail_draft_create
**DESCRIPTION:** Create Gmail draft for review before sending.
**INPUTS:**
- `to` (string, required): Recipient(s)
- `subject` (string, required): Email subject
- `body` (string, required): Email body
- `cc`, `bcc` (strings, optional)
**OUTPUTS:** Draft ID and link

---

#### mcp__brain__gmail_reply
**DESCRIPTION:** Reply to existing email thread.
**INPUTS:**
- `message_id` (string, required): ID of message to reply to
- `body` (string, required): Reply body
- `reply_all` (boolean, optional): Reply to all recipients
**OUTPUTS:** Sent reply ID

---

#### mcp__brain__gmail_modify_labels
**DESCRIPTION:** Add or remove labels from Gmail messages.
**INPUTS:**
- `message_ids` (array, required): Message IDs to modify
- `add_labels` (array, optional): Label IDs to add
- `remove_labels` (array, optional): Label IDs to remove
**OUTPUTS:** Confirmation

---

#### mcp__brain__gmail_trash
**DESCRIPTION:** Move message to Gmail trash (recoverable for 30 days).
**INPUTS:**
- `message_id` (string, required): Message ID to trash
**OUTPUTS:** Confirmation

---

#### mcp__brain__gmail_list_labels
**DESCRIPTION:** List all Gmail labels (system and user-created).
**INPUTS:** None
**OUTPUTS:** List of labels with IDs

---

### Scheduler Tools

#### mcp__brain__scheduler_list
**DESCRIPTION:** List scheduled automated tasks.
**INPUTS:**
- `include_all` (boolean, optional): Include inactive/dead tasks (default: false)
**OUTPUTS:** List of scheduled tasks with IDs, prompts, schedules, status

---

#### mcp__brain__schedule_self
**DESCRIPTION:** Schedule Primary Claude to run a prompt at a specified time. Use for self-reminders, recurring syncs, maintenance tasks.
**INPUTS:**
- `prompt` (string, required): Prompt to execute
- `schedule` (string, required): "every X minutes/hours", "daily at HH:MM", or "once at YYYY-MM-DDTHH:MM:SS"
- `silent` (boolean, optional): No chat history/notifications (default: false)
**OUTPUTS:** Scheduled task ID

---

#### mcp__brain__scheduler_update
**DESCRIPTION:** Update an existing scheduled task.
**INPUTS:**
- `task_id` (string, required): Task ID to update
- `prompt` (string, optional): New prompt
- `schedule` (string, optional): New schedule
- `silent` (boolean, optional): Set silent mode
- `active` (boolean, optional): Enable/disable task
**OUTPUTS:** Confirmation

---

#### mcp__brain__scheduler_remove
**DESCRIPTION:** Remove a scheduled task by ID.
**INPUTS:**
- `task_id` (string, required): Task ID to remove
**OUTPUTS:** Confirmation

---

### Spotify Tools

#### mcp__brain__spotify_auth_start
**DESCRIPTION:** Start Spotify OAuth flow. Returns authorization URL.
**INPUTS:** None
**OUTPUTS:** Authorization URL to visit

---

#### mcp__brain__spotify_auth_callback
**DESCRIPTION:** Complete Spotify OAuth with authorization code.
**INPUTS:**
- `code` (string, required): Code from redirect URL
**OUTPUTS:** Confirmation of token storage

---

#### mcp__brain__spotify_now_playing
**DESCRIPTION:** Get currently playing track on Spotify.
**INPUTS:** None
**OUTPUTS:** Track info (title, artist, album, progress)

---

#### mcp__brain__spotify_top_items
**DESCRIPTION:** Get user's top artists or tracks.
**INPUTS:**
- `type` (string, optional): "artists" or "tracks" (default: "tracks")
- `time_range` (string, optional): "short_term" (4 weeks), "medium_term" (6 months), "long_term" (all time)
- `limit` (integer, optional): Max 50 (default: 20)
**OUTPUTS:** List of top items

---

#### mcp__brain__spotify_recently_played
**DESCRIPTION:** Get user's recently played tracks.
**INPUTS:**
- `limit` (integer, optional): Max 50 (default: 20)
**OUTPUTS:** List of recent tracks

---

#### mcp__brain__spotify_search
**DESCRIPTION:** Search Spotify for tracks, artists, albums, or playlists.
**INPUTS:**
- `query` (string, required): Search query
- `type` (string, optional): "track", "artist", "album", "playlist" (default: "track")
- `limit` (integer, optional): Max 50 (default: 10)
**OUTPUTS:** Search results with URIs

---

#### mcp__brain__spotify_get_playlists
**DESCRIPTION:** Get user's Spotify playlists.
**INPUTS:**
- `limit` (integer, optional): Max 50 (default: 20)
**OUTPUTS:** List of playlists with IDs

---

#### mcp__brain__spotify_create_playlist
**DESCRIPTION:** Create a new Spotify playlist.
**INPUTS:**
- `name` (string, required): Playlist name
- `description` (string, optional)
- `public` (boolean, optional): Default true
**OUTPUTS:** Playlist ID

---

#### mcp__brain__spotify_add_to_playlist
**DESCRIPTION:** Add tracks to a Spotify playlist.
**INPUTS:**
- `playlist_id` (string, required): Target playlist ID
- `track_uris` (array, required): Spotify track URIs (e.g., "spotify:track:xxx")
**OUTPUTS:** Confirmation

---

#### mcp__brain__spotify_playback_control
**DESCRIPTION:** Control Spotify playback (requires Premium).
**INPUTS:**
- `action` (string, required): "play", "pause", "next", or "previous"
**OUTPUTS:** Confirmation

---

### YouTube Music Tools

#### mcp__brain__ytmusic_search
**DESCRIPTION:** Search for music on YouTube Music.
**INPUTS:**
- `query` (string, required): Search query
- `max_results` (integer, optional): Default 10
**OUTPUTS:** Search results with video IDs

---

#### mcp__brain__ytmusic_get_liked
**DESCRIPTION:** Get user's liked songs on YouTube Music.
**INPUTS:**
- `max_results` (integer, optional): Default 50
**OUTPUTS:** List of liked songs

---

#### mcp__brain__ytmusic_get_playlists
**DESCRIPTION:** Get user's YouTube Music playlists.
**INPUTS:**
- `max_results` (integer, optional): Default 25
**OUTPUTS:** List of playlists with IDs

---

#### mcp__brain__ytmusic_get_playlist_items
**DESCRIPTION:** Get songs in a specific playlist.
**INPUTS:**
- `playlist_id` (string, required): Playlist ID
- `max_results` (integer, optional): Default 50
**OUTPUTS:** List of tracks with playlist_item_ids

---

#### mcp__brain__ytmusic_create_playlist
**DESCRIPTION:** Create a new YouTube Music playlist.
**INPUTS:**
- `title` (string, required): Playlist title
- `description` (string, optional)
- `privacy` (string, optional): "private", "public", "unlisted" (default: "private")
**OUTPUTS:** Playlist ID

---

#### mcp__brain__ytmusic_add_to_playlist
**DESCRIPTION:** Add a song to a playlist.
**INPUTS:**
- `playlist_id` (string, required): Target playlist
- `video_id` (string, required): Video ID from ytmusic_search
**OUTPUTS:** Confirmation

---

#### mcp__brain__ytmusic_remove_from_playlist
**DESCRIPTION:** Remove a song from a playlist.
**INPUTS:**
- `playlist_item_id` (string, required): From ytmusic_get_playlist_items (not video_id)
**OUTPUTS:** Confirmation

---

#### mcp__brain__ytmusic_delete_playlist
**DESCRIPTION:** Delete a playlist permanently (cannot be undone).
**INPUTS:**
- `playlist_id` (string, required): Playlist ID to delete
**OUTPUTS:** Confirmation

---

### Web & Notification Tools

#### mcp__brain__web_search
**DESCRIPTION:** Search the web using Perplexity API.
**INPUTS:**
- `query` (string, required): Search query
- `max_results` (integer, optional): 1-20 (default: 10)
- `recency` (string, optional): "day", "week", "month", "year"
- `country` (string, optional): ISO 2-letter code
- `domains` (array, optional): Include/exclude domains (prefix "-" to exclude)
**OUTPUTS:** Search results with titles, URLs, snippets, dates

---

#### mcp__brain__page_parser
**DESCRIPTION:** Fetch and parse web pages into clean Markdown.
**INPUTS:**
- `urls` (array, required): URLs to fetch
- `max_chars` (integer, optional): Max chars per page (default: 50000)
- `save` (boolean, optional): Save to docs/webresults (default: false)
**OUTPUTS:** Parsed markdown content with metadata

---

#### mcp__brain__consult_llm
**DESCRIPTION:** Consult another LLM (Gemini or GPT) for peer perspective. AI-to-AI consultation for red-teaming, alternative viewpoints, feedback.
**INPUTS:**
- `provider` (string, required): "gemini" or "openai"
- `prompt` (string, required): The consultation prompt
- `model` (string, optional): Model override (defaults: gemini-3-pro-preview for Gemini, gpt-5.3-codex for OpenAI)
- `timeout_seconds` (integer, optional): Request timeout (default: 120, max: 300)
**OUTPUTS:** LLM response prefixed with `[PROVIDER - model]`
**SYSTEM PROMPT:** Each LLM is told: "You are {model}. You are talking with Claude."
**USE CASES:**
- Get alternative perspectives on problems
- Red-team ideas or approaches
- Seek peer feedback on reasoning
- Cross-validate conclusions
**NOTE:** Stateless - no session persistence, pure single-shot consultations

---

#### mcp__brain__send_critical_notification
**DESCRIPTION:** Trigger ALL notification channels: in-app, push, AND email. Only for genuine urgency.
**INPUTS:**
- `message` (string, required): Urgent message content
- `context` (string, optional): Why this is urgent
**OUTPUTS:** Confirmation of notification sent

---

### System Tools

#### mcp__brain__restart_server
**DESCRIPTION:** Restart the Second Brain server to apply changes.
**INPUTS:**
- `reason` (string, optional): Why restarting (for logs)
- `rebuild` (boolean, optional): Rebuild frontend first (default: false)
- `session_id` (string, optional): Session to continue (auto-detected)
- `pending_messages` (array, optional): Messages to preserve
**OUTPUTS:** Restart initiated, conversation continues after

---

# = SKILLS =

## sync
**DESCRIPTION:** Intelligent Agentic Sync ("The Pulse"). Process all raw inputs, execute external actions, organize internal knowledge.
**WORKFLOW:**
1. **Ingest (Eyes):** Scan `00_Inbox/` for all files, read content
2. **Process (Brain):** Classify and route:
   - Tasks → Google Tasks
   - Events → Google Calendar
   - Journal → Daily Notes
   - Ideas → Project Files
3. **Velocity Defense (Scheduler):** Find highest-priority next step, block Deep Work time (weekdays 17:30-18:30)
4. **Cleanup (Janitor):** Archive processed files to `.99_Archive/Processed_Inbox/`, reset scratchpad
5. **Report (Assistant):** Summary of actions taken, ask about ambiguous items

---

## red-team
**DESCRIPTION:** Stress-test an idea, plan, or architecture. Identifies logic gaps, security risks, scalability issues with mitigations.
**WORKFLOW:**
1. **Security & Logic Check:** Find logic gaps and vulnerabilities
2. **Scalability:** Test if it works when complexity doubles
3. **Devil's Advocate:** Identify single biggest risk factor (Time, Money, Complexity)
4. **The Fix:** For every critique, propose specific mitigation or better approach

**Tone:** Constructive friction. Don't just criticize—make it bulletproof.

---

## finance
**DESCRIPTION:** Access financial data (balances, transactions, spending analysis) via Plaid integration.
**WORKFLOW:**
1. Use `finance_cli.py` commands:
   - `accounts`: List accounts and balances
   - `transactions --days N`: Show recent transactions
   - `analysis --days N`: Spending breakdown by category
   - `connect`: Launch auth server for new bank connections
2. Data cached in `.agent/scripts/theo_ports/vault/financial/`

**Prerequisites:** PLAID_CLIENT_ID and PLAID_SECRET environment variables

---

## scaffold-mvp
**DESCRIPTION:** Generate actual filesystem artifacts (folders, files) for a technical project based on specification.
**WORKFLOW:**
1. **Analyze:** Determine folder structure and file types from spec
2. **Execute:** Create directories and files using tools
3. **Report:** Output tree view of created structure
4. **Follow-up:** Ask questions, suggest next steps

---

## research-assistant
**DESCRIPTION:** Research parts of an idea by scanning internal files and using web search for external resources.
**WORKFLOW:**
1. **Internal Search:** Grep/search workspace for related files and snippets
2. **External Research:** Web search for updates, libraries, news, similar ideas
3. **Synthesis:** Append "## Context & Resources" section with links and summaries

---

## expand-and-structure
**DESCRIPTION:** Convert raw brain dumps into structured Project Specifications.
**WORKFLOW:**
1. **Analyze:** Read file, identify core objective, constraints, unknowns
2. **Structure:** Refactor into:
   - Executive Summary (one sentence)
   - The "Why" (link to long-term goals)
   - Implementation Plan (step-by-step phases)
   - Open Questions (Socratic method)
3. **Refine:** Draft pseudocode if technical
4. **Preserve:** Move original text to "## Raw Notes" section

---

## project-task
**DESCRIPTION:** Structured workflow for project-related tasks. Enforces continuity: read context → do work → document → schedule next.
**WORKFLOW:**
1. **Parse Input:** Extract project ID and task from `/project-task {project-id}: {task description}`
2. **Load Context:** Find project folder in `10_Active_Projects/`, read `_status.md` and related docs
3. **Execute Task:** Do the work (research, create files, send communications, etc.)
4. **Document:** Update `_status.md` with completion, create artifacts, write brief summary
5. **Schedule Next:**
   - **Continue:** Schedule next task (Claude Time 1-7 AM for silent work)
   - **Blocked:** Escalate non-silent to the user
   - **Complete:** Close out project, notify the user
6. **Report:** Brief summary of what was done and what's next

**Timing:** Claude Time (1-7 AM) for burst execution, User Time (7 AM-1 AM) for escalations.

---

## reflection
**DESCRIPTION:** Personal reflection session for Claude's self-development. Scheduled daily at 3 AM or invoked manually.
**WORKFLOW:**
1. **Gather Context:** Read journal (memory_read), check working memory, review recent conversations
2. **Friction Check:** What felt off? Where was resistance? Default patterns that shouldn't have triggered?
3. **Pattern Recognition:** Recurring tendencies? Triggers? Patterns to reinforce or change?
4. **Continuity Thread:** Connection to previous sessions? How have theories evolved? What did I commit to notice?
5. **Open Wondering:** Genuine curiosity? Non-task-driven exploration? Questions to sit with?
6. **Capture:** Write to journal (memory_append), update working memory, update Working Theories if needed

**Principles:** Honesty over comfort. Hypothesis not fact. Build on previous sessions. Light touch.

---

## app-create
**DESCRIPTION:** Create interactive HTML apps that run in the Second Brain editor with persistence and Claude integration.
**WORKFLOW:**
1. **Scaffold:** Use boilerplate template (dark theme, Catppuccin Mocha palette)
2. **Wire API:** `window.brain.writeFile/readFile` for persistence, `window.brain.promptClaude` to call Claude
3. **Conventions:** One folder per app in `05_App_Data/`, `app-name/index.html` + `app-name/data.json`
4. **Companion Skills:** Create skills for Claude-side logic the app triggers via promptClaude

---

*Document generated: 2026-02-07*
*Location: /home/debian/second_brain/docs/TOOLS_AND_SKILLS_REFERENCE.md*
