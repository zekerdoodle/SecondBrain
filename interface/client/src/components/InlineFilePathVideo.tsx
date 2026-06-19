import { Video } from 'lucide-react';
import { toRelativePath } from '../utils/filePaths';

interface InlineFilePathVideoProps {
  /** Project-relative path (already normalized via toRelativePath). */
  path: string;
  /** Original backtick text or markdown src — shown if video fails to load. */
  originalText: string;
  /** Click handler for opening the video file in the editor. */
  onOpenFile?: (path: string) => void;
}

/**
 * Click-to-open video card for a video file referenced by path in chat.
 *
 * The editor owns generated-video playback; chat should not expose a partial
 * inline player that can stop before the local file ends.
 */
export function InlineFilePathVideo({
  path,
  originalText,
  onOpenFile,
}: InlineFilePathVideoProps) {
  const normalizedPath = toRelativePath(path);
  const fileName = normalizedPath.split('/').pop() || normalizedPath;

  if (!onOpenFile) {
    return <code>{originalText}</code>;
  }

  return (
    <button
      type="button"
      className="my-2 inline-flex max-w-full items-center gap-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-tertiary)] px-3 py-2 text-left text-[var(--text-primary)] shadow-sm transition-colors hover:border-[var(--accent-primary)] hover:bg-[var(--accent-light)]"
      data-video-open-card="true"
      data-video-path={normalizedPath}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onOpenFile(normalizedPath);
      }}
      title={`Open ${normalizedPath} in editor`}
    >
      <Video size={18} className="shrink-0 text-[var(--accent-primary)]" aria-hidden="true" />
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{fileName}</span>
        <span className="block truncate text-xs text-[var(--text-secondary)]">Open video in editor</span>
      </span>
    </button>
  );
}
