import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Search, X, Loader2, FileText, FileCode, Image, File as FileIcon } from 'lucide-react';
import { clsx } from 'clsx';
import { API_URL } from '../config';

interface FileSearchModalProps {
  isOpen: boolean;
  files: string[];
  onSelect: (path: string) => void;
  onClose: () => void;
}

interface SearchResult {
  path: string;
  matchType: 'filename' | 'content';
  preview?: string; // content snippet for keyword matches
}

// Debounce hook
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);
  return debouncedValue;
}

const TEXT_EXTENSIONS = new Set([
  '.md', '.txt', '.json', '.py', '.js', '.ts', '.tsx', '.jsx',
  '.yaml', '.yml', '.csv', '.html', '.css', '.toml', '.cfg',
  '.sh', '.bash', '.zsh', '.env', '.ini', '.xml', '.svg',
]);

function isTextFile(path: string): boolean {
  const dot = path.lastIndexOf('.');
  if (dot === -1) return false;
  return TEXT_EXTENSIONS.has(path.slice(dot).toLowerCase());
}

function getFileIcon(name: string) {
  if (name.endsWith('.md') || name.endsWith('.txt')) return <FileText size={14} className="text-[var(--text-secondary)]" />;
  if (name.endsWith('.py') || name.endsWith('.js') || name.endsWith('.ts') || name.endsWith('.tsx') || name.endsWith('.jsx')) return <FileCode size={14} className="text-amber-500" />;
  if (name.endsWith('.png') || name.endsWith('.jpg') || name.endsWith('.jpeg') || name.endsWith('.gif') || name.endsWith('.svg')) return <Image size={14} className="text-purple-400" />;
  return <FileIcon size={14} className="text-[var(--text-muted)]" />;
}

// Highlight matching text
function highlightMatch(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <span className="bg-[var(--accent-primary)]/25 text-[var(--accent-primary)] font-semibold rounded-sm px-0.5">
        {text.slice(idx, idx + query.length)}
      </span>
      {text.slice(idx + query.length)}
    </>
  );
}

export const FileSearchModal: React.FC<FileSearchModalProps> = ({
  isOpen,
  files,
  onSelect,
  onClose,
}) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isSearchingContent, setIsSearchingContent] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const debouncedQuery = useDebounce(query, 200);

  // Reset state when modal opens/closes
  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setResults([]);
      setSelectedIndex(0);
      setIsSearchingContent(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      // Cancel any pending content search
      abortRef.current?.abort();
    }
  }, [isOpen]);

  // Filename search (immediate, synchronous)
  const filenameResults = useMemo((): SearchResult[] => {
    const q = debouncedQuery.trim().toLowerCase();
    if (!q) return [];

    return files
      .filter(path => path.toLowerCase().includes(q))
      .sort((a, b) => {
        const aName = a.split('/').pop()!.toLowerCase();
        const bName = b.split('/').pop()!.toLowerCase();
        // Prioritize filename matches over path matches
        const aNameMatch = aName.includes(q);
        const bNameMatch = bName.includes(q);
        if (aNameMatch && !bNameMatch) return -1;
        if (!aNameMatch && bNameMatch) return 1;
        // Then by path length (shorter = closer to root = probably more relevant)
        return a.length - b.length;
      })
      .slice(0, 50)
      .map(path => ({ path, matchType: 'filename' as const }));
  }, [files, debouncedQuery]);

  // Content search fallback (async, only when no filename matches)
  useEffect(() => {
    const q = debouncedQuery.trim();

    // If we have filename results, just use those
    if (filenameResults.length > 0 || q.length < 3) {
      setResults(filenameResults);
      setSelectedIndex(0);
      setIsSearchingContent(false);
      return;
    }

    // No filename matches + query is long enough -> search content
    setResults([]);
    setIsSearchingContent(true);

    // Cancel previous content search
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const searchContent = async () => {
      const textFiles = files.filter(isTextFile).slice(0, 100);
      const matches: SearchResult[] = [];

      // Search files in parallel batches of 10
      const batchSize = 10;
      for (let i = 0; i < textFiles.length; i += batchSize) {
        if (controller.signal.aborted) return;

        const batch = textFiles.slice(i, i + batchSize);
        const promises = batch.map(async (filePath) => {
          try {
            const res = await fetch(`${API_URL}/file/${filePath}`, { signal: controller.signal });
            if (!res.ok) return null;
            const data = await res.json();
            const content = data.content || '';
            const lowerContent = content.toLowerCase();
            const idx = lowerContent.indexOf(q.toLowerCase());
            if (idx !== -1) {
              // Extract a preview snippet around the match
              const start = Math.max(0, idx - 40);
              const end = Math.min(content.length, idx + q.length + 60);
              let preview = content.slice(start, end).replace(/\n/g, ' ').trim();
              if (start > 0) preview = '...' + preview;
              if (end < content.length) preview = preview + '...';
              return { path: filePath, matchType: 'content' as const, preview };
            }
            return null;
          } catch {
            return null;
          }
        });

        const batchResults = await Promise.all(promises);
        for (const r of batchResults) {
          if (r) matches.push(r);
        }

        // Update results incrementally so user sees matches as they come in
        if (!controller.signal.aborted && matches.length > 0) {
          setResults([...matches]);
          setSelectedIndex(0);
        }
      }

      if (!controller.signal.aborted) {
        setResults(matches);
        setSelectedIndex(0);
        setIsSearchingContent(false);
      }
    };

    searchContent();

    return () => controller.abort();
  }, [debouncedQuery, filenameResults, files]);

  // Keyboard navigation
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => Math.min(prev + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && results.length > 0) {
      e.preventDefault();
      onSelect(results[selectedIndex].path);
      onClose();
    }
  }, [results, selectedIndex, onSelect, onClose]);

  // Scroll selected item into view
  useEffect(() => {
    if (!resultsRef.current) return;
    const selected = resultsRef.current.children[selectedIndex] as HTMLElement;
    if (selected) {
      selected.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 animate-modal-backdrop"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-[var(--bg-secondary)] rounded-xl shadow-2xl w-full max-w-lg mx-4 animate-modal-content border border-[var(--border-color)] overflow-hidden flex flex-col max-h-[60vh]">
        {/* Search Input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--border-color)]">
          <Search size={18} className="text-[var(--text-muted)] shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search files by name..."
            className="flex-1 bg-transparent outline-none text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]"
          />
          {isSearchingContent && <Loader2 size={16} className="animate-spin text-[var(--accent-primary)] shrink-0" />}
          <button
            onClick={onClose}
            className="p-1 rounded-md hover:bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors shrink-0"
          >
            <X size={16} />
          </button>
        </div>

        {/* Results */}
        <div ref={resultsRef} className="overflow-y-auto flex-1">
          {/* Empty state */}
          {!query && (
            <div className="flex flex-col items-center justify-center py-8 text-[var(--text-muted)]">
              <Search size={28} className="mb-2 opacity-40" />
              <p className="text-sm">Type to search files</p>
            </div>
          )}

          {/* No results */}
          {query && debouncedQuery && results.length === 0 && !isSearchingContent && (
            <div className="flex flex-col items-center justify-center py-8 text-[var(--text-muted)]">
              <p className="text-sm">No files found for "{debouncedQuery}"</p>
            </div>
          )}

          {/* Searching content indicator */}
          {isSearchingContent && results.length === 0 && (
            <div className="flex items-center gap-2 px-4 py-3 text-sm text-[var(--text-muted)]">
              <Loader2 size={14} className="animate-spin" />
              <span>Searching file contents...</span>
            </div>
          )}

          {/* Result items */}
          {results.map((result, idx) => {
            const fileName = result.path.split('/').pop()!;
            const dirPath = result.path.split('/').slice(0, -1).join('/');

            return (
              <div
                key={result.path}
                onClick={() => {
                  onSelect(result.path);
                  onClose();
                }}
                className={clsx(
                  "flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors",
                  idx === selectedIndex
                    ? "bg-[var(--accent-primary)]/10 text-[var(--text-primary)]"
                    : "hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
                )}
              >
                <span className="shrink-0">{getFileIcon(fileName)}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate text-[var(--text-primary)]">
                      {result.matchType === 'filename'
                        ? highlightMatch(fileName, debouncedQuery)
                        : fileName
                      }
                    </span>
                    {result.matchType === 'content' && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] shrink-0">
                        content
                      </span>
                    )}
                  </div>
                  {dirPath && (
                    <p className="text-xs text-[var(--text-muted)] truncate">
                      {result.matchType === 'filename'
                        ? highlightMatch(dirPath, debouncedQuery)
                        : dirPath
                      }
                    </p>
                  )}
                  {result.preview && (
                    <p className="text-xs text-[var(--text-muted)] truncate mt-0.5 italic">
                      {highlightMatch(result.preview, debouncedQuery)}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer hint */}
        {results.length > 0 && (
          <div className="px-4 py-2 border-t border-[var(--border-color)] text-[10px] text-[var(--text-muted)] flex items-center gap-3">
            <span><kbd className="px-1 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-muted)] font-mono">↑↓</kbd> navigate</span>
            <span><kbd className="px-1 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-muted)] font-mono">↵</kbd> open</span>
            <span><kbd className="px-1 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-muted)] font-mono">esc</kbd> close</span>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
};
