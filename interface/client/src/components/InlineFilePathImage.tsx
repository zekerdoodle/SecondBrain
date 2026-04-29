import { useState } from 'react';
import { API_URL } from '../config';

interface InlineFilePathImageProps {
  /** Project-relative path (already normalized via toRelativePath). */
  path: string;
  /** Original backtick text or markdown src — shown if image fails to load. */
  originalText: string;
  /** Optional alt text (for markdown ![alt](path) syntax). */
  alt?: string;
  /** Click handler for the fallback path-link if image fails to load. */
  onOpenFile?: (path: string) => void;
}

/**
 * Inline thumbnail for an image file referenced by path in chat.
 *
 * Mirrors the existing user-uploaded-image pattern (Chat.tsx:299–311):
 *   - <img> tag with API_URL/file/{path} src
 *   - loading="lazy"
 *   - same Tailwind classes
 *   - click opens raw image in a new tab
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
  const src = `${API_URL}/file/${path}`;

  if (errored) {
    // Fallback: render the same clickable path-link the chat uses elsewhere
    if (onOpenFile) {
      return (
        <code
          className="file-path-link"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onOpenFile(path);
          }}
          title={`Open ${path} in editor`}
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
      alt={alt || path}
      loading="lazy"
      className="max-h-48 max-w-full rounded-lg cursor-pointer hover:opacity-90 transition-opacity"
      onClick={() => window.open(src, '_blank')}
      onError={() => setErrored(true)}
      title={path}
    />
  );
}
