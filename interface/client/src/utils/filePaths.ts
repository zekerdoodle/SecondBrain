/**
 * File path detection and normalization for clickable links in the chat UI.
 *
 * Inline code snippets that look like file paths become clickable links
 * that open the file in the editor. This module centralises the detection
 * and normalisation logic so Chat.tsx and BlockView.tsx stay in sync.
 */

// Known top-level directories inside the Second Brain project tree.
// Paths starting with one of these (with an optional prefix like ./ or ~/second_brain/)
// are confidently identified as project files.
const KNOWN_DIRS =
  'interface|\\.claude|0[0-5]_\\w+|docs|20_Areas|10_Active_Projects|30_Incubator|40_Archive|05_App_Data';

// Strict regex: matches paths that clearly belong to the project tree.
// Handles absolute, ./, ~/, and bare relative prefixes.
const FILE_PATH_REGEX = new RegExp(
  `^(?:\\.?\\/|~\\/(?:second_brain\\/)?|\\/home\\/debian\\/second_brain\\/)?` + // optional prefix
  `(?:(?:${KNOWN_DIRS})\\/)` +                                                  // known directory
  `[\\w./@()_-]+(?:\\/[\\w./@()_-]+)*` +                                        // path segments
  `\\.\\w{1,10}$`                                                                // file extension
);

// Loose fallback: any plausible path/to/file.ext string.
// Allows ~, ./, leading /, and common special chars in filenames.
const FALLBACK_PATH_REGEX = /^[~.\/]*[\w./@()[\]{}+_-]+\/[\w./@()[\]{}+_-]+\.\w{1,10}$/;

/**
 * Decide whether an inline-code string looks like a file path.
 *
 * Designed for low false-positive rate: we require at least one slash
 * and a file extension, and we exclude URLs and strings with spaces.
 */
export function looksLikeFilePath(text: string): boolean {
  // Basic guards
  if (!text.includes('/') || !text.includes('.')) return false;
  if (text.includes(' ')) return false;
  if (/^https?:\/\//i.test(text)) return false;

  // Confident match against known project directories
  if (FILE_PATH_REGEX.test(text)) return true;

  // Fallback: generic path shape
  return FALLBACK_PATH_REGEX.test(text);
}

/**
 * Normalise any supported path format to a clean project-relative path
 * suitable for the `/api/file/{path}` endpoint.
 *
 * Handles:
 *   /home/debian/second_brain/foo  →  foo
 *   ~/second_brain/foo             →  foo
 *   ./foo                          →  foo
 *   /foo                           →  foo   (bare leading slash)
 *   foo//bar                       →  foo/bar
 */
export function toRelativePath(path: string): string {
  let p = path;

  // 1. Absolute project prefix
  const absPrefix = '/home/debian/second_brain/';
  if (p.startsWith(absPrefix)) {
    p = p.slice(absPrefix.length);
  }

  // 2. Tilde prefix  ~/second_brain/  or  ~/second_brain
  if (p.startsWith('~/second_brain/')) {
    p = p.slice('~/second_brain/'.length);
  } else if (p === '~/second_brain') {
    p = '';
  }

  // 3. Current-dir prefix  ./
  while (p.startsWith('./')) {
    p = p.slice(2);
  }

  // 4. Bare leading slash (not a full absolute path — already handled above)
  if (p.startsWith('/')) {
    p = p.slice(1);
  }

  // 5. Collapse double slashes
  p = p.replace(/\/{2,}/g, '/');

  // 6. Strip trailing slash
  if (p.endsWith('/')) {
    p = p.slice(0, -1);
  }

  return p;
}

const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'];

/**
 * Whether a path string ends with a recognized image extension.
 * Case-insensitive. Does NOT verify the file exists — the renderer's
 * onError handler is the fallback path.
 */
export function isImagePath(text: string): boolean {
  const lower = text.toLowerCase();
  return IMAGE_EXTENSIONS.some(ext => lower.endsWith(ext));
}
