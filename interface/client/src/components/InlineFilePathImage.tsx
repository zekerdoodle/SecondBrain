import { useState } from 'react';
import { API_URL } from '../config';
import { toRelativePath } from '../utils/filePaths';

interface InlineFilePathImageProps {
  /** Project-relative path (already normalized via toRelativePath). */
  path: string;
  /** Original backtick text or markdown src — shown if image fails to load. */
  originalText: string;
  /** Optional alt text (for markdown ![alt](path) syntax). */
  alt?: string;
  /** Click handler for opening the image file in the editor. */
  onOpenFile?: (path: string) => void;
}

/**
 * Inline thumbnail for an image file referenced by path in chat.
 *
 * Mirrors the existing user-uploaded-image pattern (Chat.tsx:299–311):
 *   - <img> tag with API_URL/file/{path} src
 *   - loading="lazy"
 *   - same Tailwind classes
 *   - click opens the source file in the editor when available
 *
 * Gracefully falls back to a clickable file-path-link (matching the
 * existing path-in-backticks behavior) if the image fails to load.
 */
export function InlineFilePathImage({
  path,
  originalText,
  alt,
  onOpenFile,
}: InlineFilePathImageProps) {
  const [errored, setErrored] = useState(false);
  const normalizedPath = toRelativePath(path);
  const src = `${API_URL}/file/${normalizedPath}`;

  if (errored) {
    // Fallback: render the same clickable path-link the chat uses elsewhere
    if (onOpenFile) {
      return (
        <code
          className="file-path-link"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onOpenFile(normalizedPath);
          }}
          title={`Open ${normalizedPath} in editor`}
        >
          {originalText}
        </code>
      );
    }
    return <code>{originalText}</code>;
  }

  return (
    <img
      src={src}
      alt={alt || normalizedPath}
      loading="lazy"
      className="max-h-48 max-w-full rounded-lg cursor-pointer hover:opacity-90 transition-opacity"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (onOpenFile) {
          onOpenFile(normalizedPath);
        } else {
          window.open(src, '_blank');
        }
      }}
      onError={() => setErrored(true)}
      title={onOpenFile ? `Open ${normalizedPath} in editor` : normalizedPath}
    />
  );
}
