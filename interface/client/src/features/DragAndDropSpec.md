# Feature Specification: Drag-and-Drop File Integration

## Executive Summary
Implement drag-and-drop functionality to allow users to drag files/folders from the File Explorer into the Chat Interface (as context) and the Editor (as links), streamlining the data transfer workflow.

## The "Why"
This feature reduces friction in the "Second Brain" workflow. Instead of typing paths manually or copying content, the user can intuitively "grab" a resource and use it. It bridges the gap between the file system and the cognitive layer (Chat/Editor), making the interface feel like a cohesive OS for thought.

## Implementation Plan

### Phase 1: Draggable File Tree Items
**Goal:** Make file tree items draggable and carrying payload data (file path).
- **Modify:** `FileTree.tsx` / `TreeNode` component.
- **Action:** Add `draggable` attribute.
- **Event:** `onDragStart`: Set `dataTransfer` with:
    - `text/plain`: The full file path (e.g., `/home/debian/...`).
    - `application/x-gemini-file`: JSON object with `{ name, path, type }` for internal app awareness.

### Phase 2: Chat Interface Drop Zone
**Goal:** Allow the Chat input area to accept dropped files and format them as "context attachments" (visually distinct).
- **Modify:** `Chat.tsx`.
- **Action:** Add `onDrop` and `onDragOver` handlers to the textarea container.
- **Logic:**
    - Detect `application/x-gemini-file` or `text/plain`.
    - Instead of just pasting the text string, parse the path.
    - **Visual:** Render a "chip" or "pill" in the input area (or just above it) showing the file name and icon.
    - **Data:** When sending, append the file path to the prompt string, e.g., `[CONTEXT: /path/to/file] User message...` or simply append the path text if we want to keep it simple for now.

### Phase 3: Editor Drop Zone
**Goal:** Allow the Editor to accept dropped files and insert Markdown links.
- **Modify:** `App.tsx` (where `EditorView` is defined) or extract `EditorView` to its own file first (Refactor).
- **Action:** Add `onDrop` handler to the Editor container.
- **Logic:**
    - Get path from drop event.
    - Insert text at cursor position: `[filename](path)`.
    - **Navigation:** Ensure that clicking these links in "View" mode calls `handleFileSelect(path)` instead of trying to open an external URL.

## Open Questions
1. **Link Handling in View Mode:** When a user clicks a `[link](local/path/to/file.md)` in the Markdown preview, we need to intercept that click and trigger the app's internal navigation. Does `MDEditor` support custom link handling easily?
2. **Chat "Context" vs. "Text":** You mentioned "rendering folders/files with just their name" but sending the full path.
    - *Option A:* Just insert the text `/home/debian/...` into the input box.
    - *Option B (Preferred):* Create a visual "Attachment" list above the chat box, and invisible to the user, we append the paths to the prompt.
    - *Decision:* For V1, sticking to "Text insertion" is safest/easiest, but the user requested "show up with an icon". This implies a "Rich Input" or "Attachment" state.

## Refactoring Note
`EditorView` is currently defined inside `App.tsx`. It is best practice to move this to `src/components/Editor.tsx` before adding complex drag-and-drop logic.

## Raw Notes from User
> Feature request 1:
> To be able to drag files/folders from the "Explorer" into the chat window & and into the editor.
> The 'object' that gets delivered can just be the literal path of the folder/file (since you can easily parse those), but it would be cool if those showed up with an icon next to it.
> For instance, I drag "interface.log" - My message view renders it as """[File icon] interface.log""", but you receive it as the full path (/home/debian/second_brain/interface.log)
> That same "rendering method" (rendering folders/files with just their name) should work in the editor too. I can drag and drop files and folders into the editor that act as "links". Technically under-the-hood, it's just the raw file path, but for my UI, I can click on files/folders in the editor and be directed to them
