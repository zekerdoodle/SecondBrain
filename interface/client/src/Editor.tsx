import { useState, useCallback, useMemo } from 'react';
import MDEditor from '@uiw/react-md-editor';
import { FileText, Eye, Edit2, RotateCcw, Check, ExternalLink } from 'lucide-react';
import { clsx } from 'clsx';

const isMarkdownFile = (filename: string | undefined): boolean => {
  if (!filename) return false;
  const lower = filename.toLowerCase();
  return lower.endsWith('.md') || lower.endsWith('.txt');
};

interface EditorViewProps {
  selectedFile: string | undefined;
  viewMode: 'view' | 'edit';
  setViewMode: (mode: 'view' | 'edit') => void;
  markdown: string;
  setMarkdown: (md: string) => void;
  handleSave: () => Promise<boolean>;
  handleRevert: () => void;
  hasUnsavedChanges: boolean;
  onNavigateToFile?: (path: string) => void;
  files?: string[];
}

// Check if a URL is external (http/https)
const isExternalUrl = (url: string): boolean => {
  return /^https?:\/\//i.test(url);
};

// Resolve a relative path based on current file location
const resolveRelativePath = (href: string, currentFile: string | undefined): string => {
  if (!currentFile || href.startsWith('/')) {
    // Absolute path or no current file
    return href.startsWith('/') ? href.slice(1) : href;
  }

  // Get directory of current file
  const currentDir = currentFile.includes('/')
    ? currentFile.substring(0, currentFile.lastIndexOf('/'))
    : '';

  // Handle relative paths like ./file.md or ../file.md
  let resolvedPath = href;
  if (href.startsWith('./')) {
    resolvedPath = currentDir ? `${currentDir}/${href.slice(2)}` : href.slice(2);
  } else if (href.startsWith('../')) {
    const parts = currentDir.split('/');
    let hrefParts = href.split('/');
    while (hrefParts[0] === '..') {
      parts.pop();
      hrefParts.shift();
    }
    resolvedPath = [...parts, ...hrefParts].filter(Boolean).join('/');
  } else if (!href.includes('/')) {
    // Just a filename, use current directory
    resolvedPath = currentDir ? `${currentDir}/${href}` : href;
  }

  return resolvedPath;
};

// Find a file matching wiki-style link (case-insensitive, extension optional)
const findWikiFile = (name: string, files: string[]): string | null => {
  const lowerName = name.toLowerCase();

  // Try exact match first
  const exactMatch = files.find(f => f.toLowerCase() === lowerName);
  if (exactMatch) return exactMatch;

  // Try with .md extension
  const mdMatch = files.find(f => f.toLowerCase() === `${lowerName}.md`);
  if (mdMatch) return mdMatch;

  // Try matching just the filename part
  const filenameMatch = files.find(f => {
    const filename = f.split('/').pop()?.toLowerCase() || '';
    return filename === lowerName || filename === `${lowerName}.md`;
  });
  if (filenameMatch) return filenameMatch;

  return null;
};

// Transform wiki-style [[links]] to standard markdown links
const transformWikiLinks = (content: string): string => {
  // Match [[filename]] or [[filename|display text]]
  return content.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, target, displayText) => {
    const display = displayText || target;
    return `[${display}](wiki:${target})`;
  });
};

export const EditorView: React.FC<EditorViewProps> = ({
  selectedFile,
  viewMode,
  setViewMode,
  markdown,
  setMarkdown,
  handleSave,
  handleRevert,
  hasUnsavedChanges,
  onNavigateToFile,
  files = []
}) => {
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');

  // Custom link click handler
  const handleLinkClick = useCallback((e: React.MouseEvent<HTMLAnchorElement>, href: string) => {
    // External links - let them open normally with target="_blank"
    if (isExternalUrl(href)) {
      return; // Don't prevent default, let the link open
    }

    // Internal navigation
    e.preventDefault();

    let targetPath: string | null = null;

    // Handle wiki-style links (wiki:filename)
    if (href.startsWith('wiki:')) {
      const wikiTarget = href.slice(5); // Remove 'wiki:' prefix
      targetPath = findWikiFile(wikiTarget, files);
      if (!targetPath) {
        console.warn(`Wiki link target not found: ${wikiTarget}`);
        return;
      }
    } else {
      // Regular relative/absolute path
      targetPath = resolveRelativePath(href, selectedFile);

      // Check if file exists
      if (!files.includes(targetPath)) {
        // Try with .md extension
        const withMd = `${targetPath}.md`;
        if (files.includes(withMd)) {
          targetPath = withMd;
        } else {
          console.warn(`Link target not found: ${targetPath}`);
          return;
        }
      }
    }

    if (targetPath && onNavigateToFile) {
      onNavigateToFile(targetPath);
    }
  }, [selectedFile, files, onNavigateToFile]);

  // Custom link renderer component for MDEditor.Markdown
  const CustomLink = useCallback(({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { children?: React.ReactNode }) => {
    const isExternal = href ? isExternalUrl(href) : false;
    const isWikiLink = href?.startsWith('wiki:');

    if (isExternal) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="external-link"
          {...props}
        >
          {children}
          <ExternalLink size={12} className="inline-link-icon" />
        </a>
      );
    }

    // Internal link
    return (
      <a
        href={href}
        onClick={(e) => handleLinkClick(e, href || '')}
        className={clsx('internal-link', isWikiLink && 'wiki-link')}
        {...props}
      >
        {children}
      </a>
    );
  }, [handleLinkClick]);

  // Transform markdown content to support wiki links
  const processedMarkdown = useMemo(() => {
    return transformWikiLinks(markdown);
  }, [markdown]);
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const textarea = e.currentTarget;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const indent = '  '; // 2 spaces

      if (e.shiftKey) {
        // Shift+Tab: Remove indentation from line(s)
        const beforeCursor = markdown.substring(0, start);
        const lineStart = beforeCursor.lastIndexOf('\n') + 1;
        const lineContent = markdown.substring(lineStart, end);

        if (lineContent.startsWith(indent)) {
          const newMarkdown =
            markdown.substring(0, lineStart) +
            lineContent.substring(indent.length) +
            markdown.substring(end);
          setMarkdown(newMarkdown);

          // Adjust cursor position
          setTimeout(() => {
            textarea.selectionStart = Math.max(lineStart, start - indent.length);
            textarea.selectionEnd = Math.max(lineStart, end - indent.length);
          }, 0);
        }
      } else {
        // Tab: Insert indentation
        const newMarkdown =
          markdown.substring(0, start) +
          indent +
          markdown.substring(end);
        setMarkdown(newMarkdown);

        // Move cursor after inserted indent
        setTimeout(() => {
          textarea.selectionStart = textarea.selectionEnd = start + indent.length;
        }, 0);
      }
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'link';
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const fileData = e.dataTransfer.getData('application/x-secondbrain-file');
    if (fileData && viewMode === 'edit') {
      try {
        const file = JSON.parse(fileData);
        const linkText = `[${file.name}](${file.path})`;
        
        // Insert at cursor position if possible
        const textarea = e.currentTarget as HTMLTextAreaElement;
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        
        const newText = 
          markdown.substring(0, start) + 
          linkText + 
          markdown.substring(end);
          
        setMarkdown(newText);
        
        // Restore focus (optional/tricky in React flow, but good for UX)
      } catch (err) {
        console.error("Failed to insert file link", err);
      }
    }
  };

  const onSave = async () => {
    setSaveStatus('saving');
    const success = await handleSave();
    if (success) {
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus('idle'), 1500);
    } else {
      setSaveStatus('idle');
    }
  };

  return (
    <div className="h-full flex flex-col bg-[var(--bg-secondary)] border-x border-[var(--border-color)]">
      {/* Editor Toolbar */}
      <div className="h-11 px-4 border-b border-[var(--border-color)] flex justify-between items-center bg-[var(--bg-tertiary)] shrink-0">
        <div className="flex items-center gap-2 overflow-hidden">
          <FileText size={14} className="text-[var(--text-muted)] shrink-0" />
          <span className="text-xs text-[var(--text-secondary)] truncate font-mono">{selectedFile || 'No file selected'}</span>
        </div>
        <div className="flex items-center gap-2">
          {/* View/Edit Toggle */}
          <div className="flex bg-[var(--bg-secondary)] rounded-lg border border-[var(--border-color)] p-0.5">
            <button
              onClick={() => setViewMode('view')}
              className={clsx("px-2.5 py-1 text-[11px] font-medium rounded-md flex items-center gap-1 transition-colors", viewMode === 'view' ? "bg-[var(--accent-primary)] text-white" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]")}
            >
              <Eye size={12} /> View
            </button>
            <button
              onClick={() => setViewMode('edit')}
              className={clsx("px-2.5 py-1 text-[11px] font-medium rounded-md flex items-center gap-1 transition-colors", viewMode === 'edit' ? "bg-[var(--accent-primary)] text-white" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]")}
            >
              <Edit2 size={12} /> Edit
            </button>
          </div>

          {selectedFile && viewMode === 'edit' && (
            <>
              {hasUnsavedChanges && (
                <button
                  onClick={handleRevert}
                  className="p-1.5 text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)] rounded-md transition-colors"
                  title="Discard changes"
                >
                  <RotateCcw size={14} />
                </button>
              )}
              <button
                onClick={onSave}
                disabled={saveStatus === 'saving'}
                className={clsx(
                  "px-3 py-1 text-[11px] font-medium rounded-lg transition-all shadow-sm flex items-center gap-1.5 min-w-[60px] justify-center",
                  saveStatus === 'saved'
                    ? "bg-green-500 text-white"
                    : "bg-[var(--accent-primary)] hover:bg-[var(--accent-hover)] text-white"
                )}
              >
                {saveStatus === 'saved' ? (
                  <>
                    <Check size={12} /> Saved
                  </>
                ) : saveStatus === 'saving' ? (
                  'Saving...'
                ) : (
                  'Save'
                )}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-hidden relative">
        {viewMode === 'view' ? (
          <div className="absolute inset-0 overflow-auto p-8 bg-[var(--bg-secondary)]" data-color-mode="light">
            {isMarkdownFile(selectedFile) ? (
              <div className="max-w-3xl mx-auto prose font-editor" style={{ fontFamily: 'var(--font-editor)', fontSize: 'var(--font-size-base)' }}>
                <MDEditor.Markdown
                  source={processedMarkdown}
                  style={{ backgroundColor: 'transparent', color: 'var(--text-primary)', fontFamily: 'var(--font-editor)' }}
                  components={{
                    a: CustomLink
                  }}
                />
              </div>
            ) : (
              <pre className="max-w-3xl mx-auto font-code text-sm text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed" style={{ fontFamily: 'var(--font-code)' }}>
                {markdown}
              </pre>
            )}
          </div>
        ) : (
          <textarea
            className="absolute inset-0 w-full h-full bg-[var(--bg-secondary)] text-[var(--text-primary)] p-6 text-sm resize-none focus:outline-none leading-relaxed font-editor"
            style={{ fontFamily: 'var(--font-editor)', fontSize: 'var(--font-size-base)' }}
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            onKeyDown={handleKeyDown}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            spellCheck={false}
          />
        )}
      </div>
    </div>
  );
};