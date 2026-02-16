# Second Brain Interface (V1)

A dedicated, mobile-first Progressive Web App (PWA) for interacting with your Second Brain. This interface replaces the need for disparate tools like Obsidian or SilverBullet by integrating file management, editing, and an autonomous AI agent into a single "Midnight Modern" UI.

## 🚀 Quick Start

To launch the interface:

```bash
./interface/start.sh
```

- **Frontend:** http://localhost:5173
- **Backend API:** http://localhost:8000

## ✨ Key Features

### 1. Unified Workspace
- **File Explorer:** Recursive, collapsible file tree on the left.
- **Markdown Editor:** Full-height, split-pane (edit/preview) editor powered by `@uiw/react-md-editor`.
- **Panel Toggles:** Toggle the visibility of the Explorer, Editor, or Chat independently via the global header icons.

### 2. Atlas Chat
- **Native Integration:** Wraps the `gemini` CLI directly, injecting your `GEMINI.md` context automatically.
- **Streaming Responses:** Real-time text generation.
- **Chat History:** 
  - Chats are auto-saved to disk (`.gemini/chats/*.json`).
  - Click the **History (Clock)** icon to view and load previous sessions.
- **Conversational Repair:**
  - **Edit (Pencil):** Click on any User message to edit it. This sends a "Correction" prompt to the agent, effectively repairing the conversation flow.
  - **Regenerate (Rotate):** Click on the last Assistant message to request a re-generation.

### 3. Task Scheduler
- **Background Automation:** A backend loop checks `scheduled_tasks.json` every 60 seconds.
- **Notifications:** When a task is due, a **System Notification** (Yellow Bubble) appears directly in the chat stream.
- **Management:** You can ask Atlas to "Schedule a task to [action] every [time]" directly in the chat.

## 📱 Mobile Experience
- **Touch-First Design:** Large touch targets and a bottom Tab Bar navigation on mobile devices.
- **Adaptive Layout:** Automatically switches between "3-Pane" (Desktop) and "Tabbed" (Mobile) views.

## 🛠️ Configuration
- **Theme:** "Midnight Modern" (Dark Mode by default).
- **Settings:** Configurable via `interface/client/src/index.css` (Tailwind) and `settings.json` (Gemini CLI).

## ⚠️ Notes
- **Security:** The interface binds to `0.0.0.0` to allow LAN access. Ensure you are on a trusted network.
- **Persistence:** Chat history is stored locally in `.gemini/chats/`.
