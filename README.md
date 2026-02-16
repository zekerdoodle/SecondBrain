# The Second Brain (Agent)

This is the repository for the user's "Second Brain," an AI-enhanced Personal Knowledge Management (PKM) system.

## 🌟 New Interface (V1)

**A dedicated Mobile/Desktop PWA is now available.**

To start ( or restart) the interface:
```bash
./interface/start.sh
```
*Note: This script automatically kills any previous instances running on port 8000.*

See [interface/README.md](interface/README.md) for full documentation on features and usage.

---

## Directory Structure

*   **`interface/`**: The React+FastAPI Web Application.
*   **`.gemini/`**: Agent configuration, tools, and memory.
*   **`00_Inbox/`**: Entry point for raw notes.
*   **`10_Active_Projects/`**: Current focus areas.
*   **`20_Areas/`**: Long-term responsibilities.

## Agent Capabilities

The agent ("Agent") can:
*   **Read/Write** any file in this vault.
*   **Schedule** tasks and reminders.
*   **Chat** contextually about your notes.
*   **Execute** Python scripts for data analysis.

For system instructions, see [.gemini/GEMINI.md](.gemini/GEMINI.md).