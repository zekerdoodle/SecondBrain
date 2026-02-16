import { useState, useCallback, useMemo, useRef, useEffect } from 'react';
import MDEditor from '@uiw/react-md-editor';
import { FileText, Eye, Edit2, RotateCcw, Check, ExternalLink, X } from 'lucide-react';
import { useCodeBlockWrap } from './hooks/useCodeBlockWrap';
import { clsx } from 'clsx';
import { API_URL } from './config';

const isMarkdownFile = (filename: string | undefined): boolean => {
  if (!filename) return false;
  const lower = filename.toLowerCase();
  return lower.endsWith('.md') || lower.endsWith('.txt');
};

// Brain App Bridge v2 - inject into HTML files to provide API for apps
const BRAIN_BRIDGE_SCRIPT = `
<script>
(function() {
  // Request ID counter for correlating async responses
  let _requestId = 0;
  function nextRequestId() { return 'req_' + (++_requestId) + '_' + Date.now(); }

  // Helper: send postMessage and wait for a correlated response
  function requestResponse(requestType, responseType, payload, matchFn) {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        window.removeEventListener('message', handler);
        reject(new Error(responseType + ' timed out after 120s'));
      }, 120000);
      const handler = (event) => {
        if (event.data.type === responseType && (!matchFn || matchFn(event.data))) {
          window.removeEventListener('message', handler);
          clearTimeout(timeout);
          if (event.data.success) resolve(event.data);
          else reject(new Error(event.data.error || 'Unknown error'));
        }
      };
      window.addEventListener('message', handler);
      window.parent.postMessage({ type: requestType, ...payload }, '*');
    });
  }

  window.brain = {
    // --- v1 methods (preserved) ---

    writeFile: (path, data) => {
      return requestResponse(
        'brain:writeFile',
        'brain:writeFileResponse',
        { path: path, data: typeof data === 'string' ? data : JSON.stringify(data) }
      ).then(() => undefined);
    },

    readFile: (path) => {
      return requestResponse(
        'brain:readFile',
        'brain:readFileResponse',
        { path: path },
        (d) => d.path === path
      ).then((d) => d.content);
    },

    promptClaude: (prompt) => {
      window.parent.postMessage({
        type: 'brain:promptClaude',
        prompt: prompt
      }, '*');
    },

    // --- v2 methods ---

    askClaude: (prompt, options) => {
      var reqId = nextRequestId();
      return requestResponse(
        'brain:askClaude',
        'brain:askClaudeResponse',
        { prompt: prompt, requestId: reqId, options: options || {} },
        (d) => d.requestId === reqId
      ).then((d) => d.response);
    },

    listFiles: (dirPath) => {
      return requestResponse(
        'brain:listFiles',
        'brain:listFilesResponse',
        { dirPath: dirPath || '' }
      ).then((d) => d.files);
    },

    deleteFile: (path) => {
      return requestResponse(
        'brain:deleteFile',
        'brain:deleteFileResponse',
        { path: path }
      ).then(() => undefined);
    },

    getAppInfo: () => {
      return requestResponse(
        'brain:getAppInfo',
        'brain:getAppInfoResponse',
        {}
      ).then((d) => d.appInfo);
    },

    // --- v2 methods: file watching ---

    watchFile: (path, callback, intervalMs) => {
      var watchId = 'watch_' + nextRequestId();
      // Register the callback for fileChanged events
      var handler = function(event) {
        if (event.data.type === 'brain:fileChanged' && event.data.watchId === watchId) {
          callback(event.data.content, event.data.mtime);
        }
      };
      window.addEventListener('message', handler);
      // Store handler for cleanup
      if (!window._brainWatchHandlers) window._brainWatchHandlers = {};
      window._brainWatchHandlers[watchId] = handler;
      // Tell host to start polling
      window.parent.postMessage({
        type: 'brain:watchFile',
        path: path,
        watchId: watchId,
        intervalMs: intervalMs || 2000
      }, '*');
      return watchId;
    },

    unwatchFile: (watchId) => {
      // Remove local listener
      if (window._brainWatchHandlers && window._brainWatchHandlers[watchId]) {
        window.removeEventListener('message', window._brainWatchHandlers[watchId]);
        delete window._brainWatchHandlers[watchId];
      }
      // Tell host to stop polling
      window.parent.postMessage({
        type: 'brain:unwatchFile',
        watchId: watchId
      }, '*');
    }
  };

  console.log('[Brain App Bridge v2] Initialized — askClaude, listFiles, deleteFile, getAppInfo, watchFile, unwatchFile available');
})();
</script>
`;

// Inject the brain bridge script and base href into HTML content
const injectBrainBridge = (html: string): string => {
  // Base href allows /file/ paths (and all absolute paths) to resolve against the server
  // Without this, srcdoc iframes have base URL "about:srcdoc" and can't load server resources
  const BASE_TAG = `<base href="${window.location.origin}/" />`;
  const injection = BASE_TAG + BRAIN_BRIDGE_SCRIPT;

  // Insert after <head> if present, otherwise at the start
  if (html.includes('<head>')) {
    return html.replace('<head>', '<head>' + injection);
  } else if (html.includes('<html>')) {
    return html.replace('<html>', '<html><head>' + injection + '</head>');
  } else {
    // Just prepend for fragments
    return injection + html;
  }
};

const isHtmlFile = (filename: string | undefined): boolean => {
  if (!filename) return false;
  const lower = filename.toLowerCase();
  return lower.endsWith('.html') || lower.endsWith('.htm');
};

const isImageFile = (filename: string | undefined): boolean => {
  if (!filename) return false;
  const lower = filename.toLowerCase();
  return /\.(png|jpg|jpeg|gif|webp|svg|bmp|ico)$/.test(lower);
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
  openTabs?: string[];
  onTabSelect?: (path: string) => void;
  onTabClose?: (path: string) => void;
  unsavedPaths?: Set<string>;
  /** Whether this pane is the active/focused one in split mode */
  isActive?: boolean;
  /** Callback when this pane gains focus (for split mode) */
  onPaneFocus?: () => void;
  /** Callback to close split mode (shows X button when provided) */
  onCloseSplit?: () => void;
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

// Renders HTML content in an iframe using a blob URL instead of srcDoc.
// This moves HTML parsing off the main thread, preventing UI freezes with large HTML files.
export const HtmlIframe: React.FC<{ html: string }> = ({ html }) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const blobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    // Clean up previous blob URL
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
    }

    const injected = injectBrainBridge(html);
    const blob = new Blob([injected], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    blobUrlRef.current = url;

    if (iframeRef.current) {
      iframeRef.current.src = url;
    }

    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [html]);

  return (
    <iframe
      ref={iframeRef}
      sandbox="allow-scripts allow-forms allow-modals allow-pointer-lock allow-same-origin"
      title="HTML Preview"
      className="w-full h-full border-0 bg-white"
    />
  );
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
  files = [],
  openTabs = [],
  onTabSelect,
  onTabClose,
  unsavedPaths,
  isActive,
  onPaneFocus,
  onCloseSplit,
}) => {
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');
  const editorViewRef = useRef<HTMLDivElement>(null);
  useCodeBlockWrap(editorViewRef);

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
    <div
      className={clsx(
        "h-full flex flex-col bg-[var(--bg-secondary)] border-x border-[var(--border-color)]",
        isActive === true && "ring-2 ring-[var(--accent-primary)] ring-inset"
      )}
      onClick={onPaneFocus}
    >
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

          {onCloseSplit && (
            <button
              onClick={(e) => { e.stopPropagation(); onCloseSplit(); }}
              className="p-1.5 text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-secondary)] rounded-md transition-colors ml-1"
              title="Close split view"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Tab Bar */}
      {openTabs.length > 0 && (
        <div className="h-8 bg-[var(--bg-primary)] border-b border-[var(--border-color)] flex items-stretch overflow-x-auto shrink-0 scrollbar-hide">
          {openTabs.map((tabPath) => {
            const fileName = tabPath.split('/').pop() || tabPath;
            const isActive = tabPath === selectedFile;
            const isUnsaved = unsavedPaths?.has(tabPath);
            return (
              <div
                key={tabPath}
                className={clsx(
                  "group flex items-center gap-1 pl-3 pr-1 text-[12px] font-medium cursor-pointer border-r border-[var(--border-color)] whitespace-nowrap select-none transition-colors min-w-0 shrink-0",
                  isActive
                    ? "bg-[var(--bg-secondary)] text-[var(--text-primary)] border-b-2 border-b-[var(--accent-primary)]"
                    : "bg-[var(--bg-primary)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]"
                )}
                title={tabPath}
                onClick={() => onTabSelect?.(tabPath)}
                onMouseDown={(e) => {
                  // Middle-click to close
                  if (e.button === 1) {
                    e.preventDefault();
                    onTabClose?.(tabPath);
                  }
                }}
              >
                <span className="truncate max-w-[150px]">
                  {isUnsaved && (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--accent-primary)] mr-1.5 align-middle" />
                  )}
                  {fileName}
                </span>
                <button
                  className={clsx(
                    "p-0.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors ml-1 shrink-0",
                    isActive ? "text-[var(--text-muted)] hover:text-[var(--text-primary)]" : "text-transparent group-hover:text-[var(--text-muted)] hover:!text-[var(--text-primary)]"
                  )}
                  onClick={(e) => {
                    e.stopPropagation();
                    onTabClose?.(tabPath);
                  }}
                  title="Close tab"
                >
                  <X size={12} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Content Area */}
      <div className="flex-1 overflow-hidden relative">
        {viewMode === 'view' ? (
          <div ref={editorViewRef} className="absolute inset-0 overflow-auto p-8 bg-[var(--bg-secondary)]" data-color-mode="light">
            {isMarkdownFile(selectedFile) ? (
              <div className="prose font-editor w-full max-w-none" style={{ fontFamily: 'var(--font-editor)', fontSize: 'var(--font-size-base)' }}>
                <MDEditor.Markdown
                  source={processedMarkdown}
                  style={{ backgroundColor: 'transparent', color: 'var(--text-primary)', fontFamily: 'var(--font-editor)' }}
                  components={{
                    a: CustomLink
                  }}
                />
              </div>
            ) : isHtmlFile(selectedFile) ? (
              <HtmlIframe html={markdown} />
            ) : isImageFile(selectedFile) ? (
              <div className="flex items-center justify-center h-full p-4">
                <img
                  src={`${API_URL}/raw/${selectedFile!}`}
                  alt={selectedFile!.split('/').pop() || ''}
                  loading="lazy"
                  className="max-w-full max-h-full object-contain rounded-lg shadow-lg"
                />
              </div>
            ) : (
              <pre className="w-full max-w-none font-code text-sm text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed" style={{ fontFamily: 'var(--font-code)' }}>
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