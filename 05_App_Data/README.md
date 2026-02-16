# App Data

This folder contains data persisted by interactive HTML apps running in the Second Brain editor.

## How it works

HTML files in this folder (or anywhere in the vault) have access to the Brain App Bridge API:

```javascript
// Save data
await window.brain.writeFile('my_app/data.json', { items: [] });

// Load data
const data = await window.brain.readFile('my_app/data.json');

// Ask Claude for help
window.brain.promptClaude('Help me with my notes');
```

## Conventions

- Each app should use its own subfolder: `app_name/`
- App HTML files can live here or anywhere in the vault
- Data files (JSON, etc) should live in this folder
- Use `/app-create` skill for the full app template

## Security

- All paths are sandboxed to this folder
- No directory traversal allowed
- Files are stored as plain text/JSON
