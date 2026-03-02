---
name: app-create
description: Create interactive HTML apps that run in the Second Brain editor with persistence and Claude integration.
updated: 2026-02-15
---

# App Creation Guide

Use this guide when building interactive apps for the Second Brain editor.

## Shared Libraries (Recommended)

Always include the shared UI kit for consistent theming and utilities:

```html
<link rel="stylesheet" href="/file/05_App_Data/_shared/theme.css">
<script src="/file/05_App_Data/_shared/brain-kit.js"></script>
```

- **theme.css** — Full Catppuccin Mocha palette as CSS custom properties + base reset + component styles (cards, buttons, inputs, tabs, modals, toasts, alerts, toggles, empty states, utility classes)
- **brain-kit.js** — `BrainKit.store()`, `.toast()`, `.modal()`, `.tabs()`, `.askClaude()`, `.router()` helpers

## Boilerplate Structure

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>App Name</title>
  <link rel="stylesheet" href="/file/05_App_Data/_shared/theme.css">
</head>
<body>
  <div class="card">
    <h2>App Name</h2>
    <!-- App content here -->
  </div>

  <script src="/file/05_App_Data/_shared/brain-kit.js"></script>
  <script>
    // Create a namespaced store for this app
    const store = BrainKit.store('app-name');

    // Load saved state on startup
    async function init() {
      const data = await store.read('state.json', { items: [] });
      render(data);
    }

    // Save state
    async function save(data) {
      await store.write('state.json', data);
      BrainKit.toast('Saved!', 'success');
    }

    // Render current state to UI
    function render(data) {
      // Update DOM based on state
    }

    init();
  </script>
</body>
</html>
```

## Brain Bridge API Reference

### Core APIs (window.brain)

These are injected automatically into all HTML apps by the editor.

#### writeFile(path, data)
- `path`: Relative to 05_App_Data/ (e.g., 'my_app/data.json')
- `data`: String or object (objects are JSON.stringify'd)
- Returns: Promise<void>

#### readFile(path)
- `path`: Relative to 05_App_Data/
- Returns: Promise<string> (file contents)

#### promptClaude(prompt) — v1, fire-and-forget
- `prompt`: String to send to Claude (appears in chat)
- Use `/skill-name` prefix for structured requests
- No return value

#### askClaude(prompt, options?) — v2, request-response
- `prompt`: String prompt for Claude
- `options.systemHint`: Optional system context
- Returns: Promise<string> (Claude's response)

#### listFiles(dirPath?)
- `dirPath`: Relative to 05_App_Data/ (default: root)
- Returns: Promise<FileEntry[]> with `{ name, path, isDir, size }`

#### deleteFile(path)
- `path`: Relative to 05_App_Data/
- Returns: Promise<void>

#### getAppInfo()
- Returns: Promise<AppManifest> with full app registry

#### watchFile(path, callback, intervalMs?)
- `path`: Relative to 05_App_Data/
- `callback`: `(content: string, mtime: number) => void` — called on every change
- `intervalMs`: Polling interval (default: 2000ms)
- Returns: `watchId` string — pass to `unwatchFile()` to stop

#### unwatchFile(watchId)
- Stops watching a file previously started with `watchFile()`

### BrainKit Helpers (brain-kit.js)

Higher-level wrappers — requires including `brain-kit.js`:

#### BrainKit.store(namespace)
Namespaced JSON persistence:
```js
const store = BrainKit.store('my-app');
const data = await store.read('config.json', {});   // with fallback
await store.write('config.json', data);              // pretty-printed JSON
const files = await store.list();                    // list files in namespace
await store.remove('old.json');                      // delete a file
const all = await store.readAll(['a.json', 'b.json'], null);  // parallel read
```

#### BrainKit.toast(message, type?, ms?)
Auto-dismissing notification: `'success'` | `'error'` | `'warning'`

#### BrainKit.modal({ title, subtitle?, options })
Promise-based choice dialog. Returns selected `value` or `null` if dismissed.

#### BrainKit.tabs({ bar, pages, active?, onSwitch? })
Tab switching helper. Returns `{ switchTo(tabId) }`.

#### BrainKit.askClaude(prompt, { json? })
Convenience wrapper that optionally parses JSON responses (strips markdown fences).

#### BrainKit.router({ routes, el?, onNotFound? })
Hash-based page router for multi-view apps:
```js
const router = BrainKit.router({
  routes: {
    '':           renderHome,       // default route
    'settings':   renderSettings,
    'item/:id':   renderItem        // :id captured as params.id
  },
  el: '#app'
});
router.navigate('item/42');
```
Route handlers receive `{ params, el }`.

## CSS Component Classes (from theme.css)

- `.card` — Surface card with padding and border radius
- `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger` — Button styles
- `.field`, `.field label`, `.field input` — Form field layout
- `.input` — Styled text input
- `.tabs`, `.tab-btn`, `.tab-btn.active` — Tab bar
- `.toggle` — Toggle switch (checkbox-based)
- `.modal-overlay`, `.modal` — Modal dialog
- `.toast` — Toast notification
- `.alert`, `.alert-info`, `.alert-warning`, `.alert-error` — Alert boxes
- `.empty-state` — Placeholder for empty content
- `.fade-in`, `.slide-up` — Animations
- Utility: `.flex`, `.flex-col`, `.gap-sm`, `.gap-md`, `.gap-lg`, `.text-muted`, `.text-sm`, `.mt-*`, `.mb-*`, `.p-*`

## Conventions

1. **File organization**: One folder per app in `05_App_Data/`
2. **Naming**: `app-name/index.html` + data files in same folder
3. **Styling**: Use shared `theme.css` — no inline color values
4. **State management**: Use `BrainKit.store()` for all persistence
5. **Skills**: Create companion skills for Claude-side logic (e.g., /practice-review)
6. **Registration**: Add app to `05_App_Data/apps.json` for the app launcher

## Vite Template (for Complex Apps)

For apps that need build tooling, use the scaffold:
```bash
cd /home/debian/second_brain/05_App_Data/_template
bash scaffold.sh my-app "My App" "Description"
cd ../my-app
npm run build  # produces single index.html
```

## Example Apps

- `05_App_Data/agent-builder/index.html` — Uses shared kit (cards, buttons, toggles, tabs)
- `05_App_Data/_template/src/index.html` — Vite starter with BrainKit.store() and toast()
