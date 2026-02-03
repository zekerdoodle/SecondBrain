# Second Brain Interface

A mobile-first Progressive Web App (PWA) for interacting with your Second Brain. This interface integrates file management, editing, and an autonomous AI agent into a single modern UI.

## Quick Start

```bash
./interface/start.sh
```

- **Frontend:** http://localhost:5173
- **Backend API:** http://localhost:8000

## Key Features

### Unified Workspace
- **File Explorer:** Recursive, collapsible file tree on the left
- **Markdown Editor:** Full-height, split-pane (edit/preview) editor powered by `@uiw/react-md-editor`
- **Panel Toggles:** Toggle the visibility of the Explorer, Editor, or Chat independently

### Chat Interface
- **Claude Integration:** Wraps the Claude Agent SDK with full tool access
- **Streaming Responses:** Real-time text generation
- **Chat History:** Auto-saved to disk, accessible via History icon
- **Conversational Repair:**
  - **Edit:** Click any User message to edit and resend
  - **Regenerate:** Request a new response for the last message

### Task Scheduler
- **Background Automation:** Backend checks scheduled tasks every 60 seconds
- **Notifications:** System notifications appear in the chat stream when tasks are due
- **Management:** Schedule tasks via natural language ("Schedule a task to [action] every [time]")

## Mobile Experience
- **Touch-First Design:** Large touch targets and bottom Tab Bar navigation on mobile
- **Adaptive Layout:** Automatically switches between 3-pane (Desktop) and tabbed (Mobile) views

## Configuration
- **Theme:** Dark mode by default ("Midnight Modern")
- **Settings:** CSS in `client/src/index.css` (Tailwind)

## Notes
- **Security:** The interface binds to `0.0.0.0` for LAN access. Use on trusted networks only.
- **Persistence:** Chat history is stored in `.claude/chats/`
