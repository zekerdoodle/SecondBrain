import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { FileTree } from './FileTree';
import { Chat } from './Chat';
import { EditorView, HtmlIframe } from './Editor';
import { AppDrawer } from './components/AppDrawer';
import { InputModal } from './components/InputModal';
import { ConfirmModal } from './components/ConfirmModal';
import { SettingsModal, useThemeInit } from './components/SettingsModal';
import { FileSearchModal } from './components/FileSearchModal';
import { useClaude } from './useClaude';
import { useToast } from './Toast';
import { Menu, FileText, MessageSquare, Sidebar, PanelRight, Settings, Layout, Columns, ArrowLeft } from 'lucide-react';
import { clsx } from 'clsx';
import { API_URL } from './config';

// Modal state types
interface InputModalState {
  isOpen: boolean;
  title: string;
  placeholder: string;
  initialValue: string;
  submitLabel: string;
  onSubmit: (value: string) => void;
}

interface ConfirmModalState {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
}

const useIsMobile = () => {
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  useEffect(() => {
    // Debounce resize events to prevent rapid state changes during window dragging
    let timeoutId: number | null = null;
    const handleResize = () => {
      if (timeoutId) window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(() => {
        setIsMobile(window.innerWidth < 768);
      }, 150); // Wait 150ms after resize stops
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      if (timeoutId) window.clearTimeout(timeoutId);
    };
  }, []);
  return isMobile;
};

function App() {
  // Initialize theme from localStorage on mount
  useThemeInit();

  const isMobile = useIsMobile();
  const [files, setFiles] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | undefined>();
  const [markdown, setMarkdown] = useState<string>('# Welcome\nSelect a file to start.');
  // Cache for unsaved changes per file
  const [draftContent, setDraftContent] = useState<Record<string, string>>({});
  // Open editor tabs - ordered list of file paths
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  // Track the "clean" content (last saved/loaded) to detect changes
  const [savedContent, setSavedContent] = useState<string>('');
  // Theme customizer modal
  const [showThemeCustomizer, setShowThemeCustomizer] = useState(false);
  // File search modal
  const [showFileSearch, setShowFileSearch] = useState(false);

  // Visibility Toggles
  const [showLeftPanel, setShowLeftPanel] = useState(true);
  const [showCenterPanel, setShowCenterPanel] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(true);

  // Editor Mode: 'view' (formatted) or 'edit' (raw text)
  const [viewMode, setViewMode] = useState<'view' | 'edit'>('view');

  // Split editor state (desktop only)
  const [isSplitMode, setIsSplitMode] = useState(false);
  const [activePaneId, setActivePaneId] = useState<'primary' | 'secondary'>('primary');
  // Secondary pane state
  const [secondaryFile, setSecondaryFile] = useState<string | undefined>();
  const [secondaryMarkdown, setSecondaryMarkdown] = useState<string>('');
  const [secondarySavedContent, setSecondarySavedContent] = useState<string>('');
  const [secondaryOpenTabs, setSecondaryOpenTabs] = useState<string[]>([]);
  const [secondaryViewMode, setSecondaryViewMode] = useState<'view' | 'edit'>('view');

  const [activeTab, setActiveTab] = useState<'files' | 'editor' | 'chat'>('files');

  // Split chat state (desktop only)
  const [isChatSplit, setIsChatSplit] = useState(false);
  const [activeChatPanel, setActiveChatPanel] = useState<'primary' | 'secondary'>('primary');

  // Full-screen app mode
  const [fullscreenApp, setFullscreenApp] = useState<{
    path: string;
    name: string;
    html: string;
  } | null>(null);

  // Chat panel width (percentage of viewport)
  const [chatWidth, setChatWidth] = useState(30);
  const isResizing = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Modal states
  const [inputModal, setInputModal] = useState<InputModalState>({
    isOpen: false,
    title: '',
    placeholder: '',
    initialValue: '',
    submitLabel: 'Create',
    onSubmit: () => {},
  });

  const [confirmModal, setConfirmModal] = useState<ConfirmModalState>({
    isOpen: false,
    title: '',
    message: '',
    confirmLabel: 'Confirm',
    onConfirm: () => {},
  });

  const closeInputModal = () => setInputModal(prev => ({ ...prev, isOpen: false }));
  const closeConfirmModal = () => setConfirmModal(prev => ({ ...prev, isOpen: false }));

  // Toast hook for split view warnings
  const { showToast } = useToast();

  // Secondary chat hook for split view (always called — React requires unconditional hooks)
  const secondaryClaude = useClaude({
    instanceId: 'secondary',
    enabled: isChatSplit,
    suppressGlobalEvents: true,
  });

  // Handler: open a chat session in the split view secondary panel
  const handleSplitChat = useCallback((sessionId: string) => {
    if (isMobile) return; // No split on mobile
    if (!sessionId || sessionId === 'new') {
      showToast({ type: 'info', title: 'Cannot split', message: 'Save a chat first before opening in split view', duration: 3000 });
      return;
    }
    setIsChatSplit(true);
    // Small delay to let WebSocket connect, then load the session
    setTimeout(() => {
      secondaryClaude.loadChat(sessionId);
    }, 150);
  }, [isMobile, secondaryClaude, showToast]);

  // Handler: open a chat session in a pop-out window
  const handlePopoutChat = useCallback((sessionId: string) => {
    if (!sessionId || sessionId === 'new') {
      showToast({ type: 'info', title: 'Cannot pop out', message: 'Save a chat first before opening in a new window', duration: 3000 });
      return;
    }
    const width = 480;
    const height = 720;
    const left = window.screenX + window.outerWidth - width - 40;
    const top = window.screenY + 60;
    window.open(
      `/chat/${sessionId}`,
      `chat-popout-${sessionId}`,
      `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no`
    );
  }, [showToast]);

  // Listen for pop-out window events via BroadcastChannel
  useEffect(() => {
    const channel = new BroadcastChannel('second-brain-popout');
    channel.onmessage = (event) => {
      if (event.data.type === 'popout-closed') {
        // Pop-out was closed — could refresh state if needed
        console.log(`Pop-out closed for session: ${event.data.sessionId}`);
      }
    };
    return () => channel.close();
  }, []);

  // Close split and force-disable on mobile
  useEffect(() => {
    if (isMobile && isChatSplit) {
      setIsChatSplit(false);
    }
  }, [isMobile, isChatSplit]);

  // Compute which files have unsaved changes
  const unsavedPaths = useMemo(() => {
    const paths = new Set(Object.keys(draftContent));
    // Also include current file if it has unsaved changes
    if (selectedFile && markdown !== savedContent) {
      paths.add(selectedFile);
    }
    // Include secondary pane file if it has unsaved changes
    if (isSplitMode && secondaryFile && secondaryMarkdown !== secondarySavedContent) {
      paths.add(secondaryFile);
    }
    return paths;
  }, [draftContent, selectedFile, markdown, savedContent, isSplitMode, secondaryFile, secondaryMarkdown, secondarySavedContent]);

  // Custom resize handler for Chat panel
  const startChatResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing.current || !containerRef.current) return;
      const containerRect = containerRef.current.getBoundingClientRect();
      const newWidth = ((containerRect.right - e.clientX) / containerRect.width) * 100;
      // Clamp between 20% and 50%
      setChatWidth(Math.min(50, Math.max(20, newWidth)));
    };

    const handleMouseUp = () => {
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }, []);

  const refreshFiles = () => {
    fetch(`${API_URL}/files`)
      .then(res => res.json())
      .then(data => setFiles(data.files))
      .catch(err => console.error(err));
  };

  // Load default editor file on initial mount
  useEffect(() => {
    const loadInitialFile = async () => {
      // First refresh the file list
      refreshFiles();

      // Then check for default editor file setting
      try {
        const res = await fetch(`${API_URL}/ui-config`);
        if (res.ok) {
          const config = await res.json();
          if (config.default_editor_file) {
            // Load the default file
            const fileRes = await fetch(`${API_URL}/file/${config.default_editor_file}`);
            if (fileRes.ok) {
              const data = await fileRes.json();
              const content = data.content || '';
              setSelectedFile(config.default_editor_file);
              setMarkdown(content);
              setSavedContent(content);
              setOpenTabs([config.default_editor_file]);
            }
          }
        }
      } catch (e) {
        console.error('Failed to load default editor file:', e);
      }
    };

    loadInitialFile();
  }, []);

  // Auto-refresh file list every 10 seconds
  useEffect(() => {
    const interval = setInterval(refreshFiles, 10_000);
    return () => clearInterval(interval);
  }, []);

  // App registry for fullscreen mode
  const [appRegistry, setAppRegistry] = useState<{ name: string; icon: string; entry: string; description: string }[]>([]);
  useEffect(() => {
    fetch(`${API_URL}/apps`).then(r => r.json()).then(data => {
      if (Array.isArray(data)) setAppRegistry(data);
    }).catch(() => {});
  }, []);

  // Open an app in fullscreen mode
  const openAppFullscreen = useCallback(async (filePath: string) => {
    try {
      const res = await fetch(`${API_URL}/file/${filePath}`);
      const data = await res.json();
      const html = data.content || '';
      const entryPath = filePath.replace('05_App_Data/', '');
      const app = appRegistry.find(a => a.entry === entryPath);
      setFullscreenApp({
        path: filePath,
        name: app?.name || filePath.split('/').slice(-2, -1)[0] || 'App',
        html,
      });
    } catch (err) {
      console.error('Failed to open app fullscreen:', err);
    }
  }, [appRegistry]);

  // Escape key exits fullscreen app mode
  useEffect(() => {
    if (!fullscreenApp) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setFullscreenApp(null);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [fullscreenApp]);

  // Keyboard shortcut: Ctrl+\ to toggle chat split view
  useEffect(() => {
    const handleSplitShortcut = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === '\\') {
        e.preventDefault();
        if (!isMobile) {
          setIsChatSplit(prev => !prev);
        }
      }
    };
    window.addEventListener('keydown', handleSplitShortcut);
    return () => window.removeEventListener('keydown', handleSplitShortcut);
  }, [isMobile]);

  // Ref to allow handleCloseTab to call handleFileSelect without circular dependency
  const handleFileSelectRef = useRef<(path: string) => void>(() => {});

  const handleFileSelect = async (path: string) => {
    // Save current file's draft if it has unsaved changes
    if (selectedFile && markdown !== savedContent) {
      setDraftContent(prev => ({ ...prev, [selectedFile]: markdown }));
    }

    setSelectedFile(path);

    // Add to open tabs if not already present
    setOpenTabs(prev => prev.includes(path) ? prev : [...prev, path]);

    // Check if there's a draft for the new file
    if (draftContent[path] !== undefined) {
      setMarkdown(draftContent[path]);
      // Fetch the saved content to track what's "clean"
      try {
        const res = await fetch(`${API_URL}/file/${path}`);
        const data = await res.json();
        setSavedContent(data.content || '');
      } catch (e) {
        console.error(e);
      }
    } else {
      // No draft, fetch from server
      try {
        const res = await fetch(`${API_URL}/file/${path}`);
        const data = await res.json();
        const content = data.content || '';
        setMarkdown(content);
        setSavedContent(content);
      } catch (e) {
        console.error(e);
      }
    }

    if (isMobile) setActiveTab('editor');
  };

  // Keep ref in sync so handleCloseTab can call it
  handleFileSelectRef.current = handleFileSelect;

  const handleCloseTab = useCallback((path: string) => {
    setOpenTabs(prev => {
      const idx = prev.indexOf(path);
      const next = prev.filter(p => p !== path);

      // If we're closing the active tab, switch to an adjacent one
      if (path === selectedFile) {
        if (next.length === 0) {
          // No tabs left
          setSelectedFile(undefined);
          setMarkdown('');
          setSavedContent('');
        } else {
          // Pick the tab to the left, or the first one if we closed the leftmost
          const newIdx = Math.min(idx, next.length - 1);
          const newPath = next[Math.max(0, newIdx)];
          // Use microtask to avoid state conflicts within setOpenTabs
          queueMicrotask(() => handleFileSelectRef.current(newPath));
        }
      }

      return next;
    });
  }, [selectedFile]);

  const handleSave = async (): Promise<boolean> => {
    if (!selectedFile) return false;
    try {
      await fetch(`${API_URL}/file/${selectedFile}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: selectedFile, content: markdown })
      });
      // Update saved content and clear draft
      setSavedContent(markdown);
      setDraftContent(prev => {
        const next = { ...prev };
        delete next[selectedFile];
        return next;
      });
      return true;
    } catch (e) {
      console.error(e);
      return false;
    }
  };

  const handleRevert = () => {
    if (!selectedFile) return;
    setMarkdown(savedContent);
    // Clear draft since we're reverting
    setDraftContent(prev => {
      const next = { ...prev };
      delete next[selectedFile];
      return next;
    });
  };

  // --- Secondary Pane File Operations ---
  const handleSecondaryFileSelect = async (path: string) => {
    // Save current secondary file's draft if it has unsaved changes
    if (secondaryFile && secondaryMarkdown !== secondarySavedContent) {
      setDraftContent(prev => ({ ...prev, [secondaryFile]: secondaryMarkdown }));
    }

    setSecondaryFile(path);
    setSecondaryOpenTabs(prev => prev.includes(path) ? prev : [...prev, path]);

    if (draftContent[path] !== undefined) {
      setSecondaryMarkdown(draftContent[path]);
      try {
        const res = await fetch(`${API_URL}/file/${path}`);
        const data = await res.json();
        setSecondarySavedContent(data.content || '');
      } catch (e) {
        console.error(e);
      }
    } else {
      try {
        const res = await fetch(`${API_URL}/file/${path}`);
        const data = await res.json();
        const content = data.content || '';
        setSecondaryMarkdown(content);
        setSecondarySavedContent(content);
      } catch (e) {
        console.error(e);
      }
    }
  };

  const handleSecondaryCloseTab = useCallback((path: string) => {
    setSecondaryOpenTabs(prev => {
      const idx = prev.indexOf(path);
      const next = prev.filter(p => p !== path);

      if (path === secondaryFile) {
        if (next.length === 0) {
          setSecondaryFile(undefined);
          setSecondaryMarkdown('');
          setSecondarySavedContent('');
        } else {
          const newIdx = Math.min(idx, next.length - 1);
          const newPath = next[Math.max(0, newIdx)];
          queueMicrotask(() => handleSecondaryFileSelect(newPath));
        }
      }
      return next;
    });
  }, [secondaryFile, draftContent]);

  const handleSecondarySave = async (): Promise<boolean> => {
    if (!secondaryFile) return false;
    try {
      await fetch(`${API_URL}/file/${secondaryFile}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: secondaryFile, content: secondaryMarkdown })
      });
      setSecondarySavedContent(secondaryMarkdown);
      setDraftContent(prev => {
        const next = { ...prev };
        delete next[secondaryFile];
        return next;
      });
      return true;
    } catch (e) {
      console.error(e);
      return false;
    }
  };

  const handleSecondaryRevert = () => {
    if (!secondaryFile) return;
    setSecondaryMarkdown(secondarySavedContent);
    setDraftContent(prev => {
      const next = { ...prev };
      delete next[secondaryFile];
      return next;
    });
  };

  // Route file selection to the active pane when in split mode
  const handleFileSelectRouted = async (path: string) => {
    if (isSplitMode && activePaneId === 'secondary') {
      await handleSecondaryFileSelect(path);
    } else {
      await handleFileSelect(path);
    }
    if (isMobile) setActiveTab('editor');
  };

  // Toggle split mode
  const toggleSplitMode = useCallback(() => {
    setIsSplitMode(prev => {
      if (prev) {
        // Closing split mode - save secondary draft if needed
        if (secondaryFile && secondaryMarkdown !== secondarySavedContent) {
          setDraftContent(d => ({ ...d, [secondaryFile]: secondaryMarkdown }));
        }
        setActivePaneId('primary');
        return false;
      }
      return true;
    });
  }, [secondaryFile, secondaryMarkdown, secondarySavedContent]);

  // --- File Operations ---

  const handleCreateFile = (parentPath?: string) => {
    const prefix = parentPath ? `${parentPath}/` : '';
    setInputModal({
      isOpen: true,
      title: 'New File',
      placeholder: 'e.g. notes.md or folder/notes.md',
      initialValue: prefix,
      submitLabel: 'Create',
      onSubmit: async (name) => {
        closeInputModal();
        await fetch(`${API_URL}/file/${name}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: name, content: '' })
        });
        refreshFiles();
        // Save current draft before switching
        if (selectedFile && markdown !== savedContent) {
          setDraftContent(prev => ({ ...prev, [selectedFile]: markdown }));
        }
        setSelectedFile(name);
        setOpenTabs(prev => prev.includes(name) ? prev : [...prev, name]);
        setMarkdown('');
        setSavedContent('');
        if (isMobile) setActiveTab('editor');
      },
    });
  };

  const handleCreateFolder = (parentPath?: string) => {
    const prefix = parentPath ? `${parentPath}/` : '';
    setInputModal({
      isOpen: true,
      title: 'New Folder',
      placeholder: 'e.g. projects',
      initialValue: prefix,
      submitLabel: 'Create',
      onSubmit: async (name) => {
        closeInputModal();
        // Create folder by creating a .gitkeep file inside it
        const folderPath = name.endsWith('/') ? name : `${name}/`;
        await fetch(`${API_URL}/file/${folderPath}.gitkeep`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: `${folderPath}.gitkeep`, content: '' })
        });
        refreshFiles();
      },
    });
  };

  const handleRename = (path: string) => {
    const currentName = path.split('/').pop() || '';
    setInputModal({
      isOpen: true,
      title: 'Rename',
      placeholder: 'Enter new name',
      initialValue: currentName,
      submitLabel: 'Rename',
      onSubmit: async (newName) => {
        closeInputModal();
        await fetch(`${API_URL}/rename`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, new_name: newName })
        });
        refreshFiles();
        if (selectedFile === path) setSelectedFile(undefined);
      },
    });
  };

  const handleDelete = (path: string) => {
    const isFolder = !path.includes('.') || path.endsWith('/');
    setConfirmModal({
      isOpen: true,
      title: `Delete ${isFolder ? 'Folder' : 'File'}`,
      message: `Are you sure you want to delete "${path}"? This action cannot be undone.`,
      confirmLabel: 'Delete',
      onConfirm: async () => {
        closeConfirmModal();
        await fetch(`${API_URL}/file/${path}`, { method: 'DELETE' });
        refreshFiles();
        // Clean up draft and tab for deleted file
        setDraftContent(prev => {
          const next = { ...prev };
          delete next[path];
          return next;
        });
        setOpenTabs(prev => prev.filter(p => p !== path));
        if (selectedFile === path) {
          setSelectedFile(undefined);
          setMarkdown('');
          setSavedContent('');
        }
      },
    });
  };

  const handleCopyPath = (path: string) => {
    navigator.clipboard.writeText(path);
  };

  const handleMoveFile = async (sourcePath: string, destinationFolder: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_URL}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: sourcePath, destination: destinationFolder })
      });
      if (!res.ok) {
        const data = await res.json();
        console.error('Move failed:', data.detail);
        return false;
      }
      const data = await res.json();
      const newPath = data.new_path;

      // Update open tabs and selected file to point to the new path
      if (selectedFile === sourcePath || selectedFile?.startsWith(sourcePath + '/')) {
        const updatedPath = selectedFile === sourcePath
          ? newPath
          : newPath + selectedFile.slice(sourcePath.length);
        setSelectedFile(updatedPath);
      }

      setOpenTabs(prev => prev.map(tab => {
        if (tab === sourcePath) return newPath;
        if (tab.startsWith(sourcePath + '/')) return newPath + tab.slice(sourcePath.length);
        return tab;
      }));

      // Update draft content keys
      setDraftContent(prev => {
        const updated: Record<string, string> = {};
        for (const [key, val] of Object.entries(prev)) {
          if (key === sourcePath) {
            updated[newPath] = val;
          } else if (key.startsWith(sourcePath + '/')) {
            updated[newPath + key.slice(sourcePath.length)] = val;
          } else {
            updated[key] = val;
          }
        }
        return updated;
      });

      refreshFiles();
      return true;
    } catch (e) {
      console.error('Move failed:', e);
      return false;
    }
  };

  // --- File Upload ---
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadTargetDir = useRef<string>('');

  const uploadFiles = async (targetDir: string, fileList: FileList | File[]) => {
    const formData = new FormData();
    for (const file of fileList) {
      formData.append('files', file);
    }
    await fetch(`${API_URL}/upload/${targetDir}`, {
      method: 'POST',
      body: formData,
    });
    refreshFiles();
  };

  const handleUploadFiles = (parentPath?: string, files?: FileList | File[]) => {
    if (files && files.length > 0) {
      uploadFiles(parentPath || '', files);
    } else {
      uploadTargetDir.current = parentPath || '';
      fileInputRef.current?.click();
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      uploadFiles(uploadTargetDir.current, files);
    }
    // Reset so the same file can be re-uploaded
    e.target.value = '';
  };

  // --- Views ---

  const Header = () => (
    <div className="h-12 bg-[var(--bg-secondary)] border-b border-[var(--border-color)] flex items-center justify-between px-4 shrink-0 shadow-warm">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-[var(--text-primary)] tracking-tight select-none">Second Brain</span>
      </div>

      <div className="flex items-center gap-1">
        {/* Panel Toggles Group */}
        <div className="flex bg-[var(--bg-tertiary)] rounded-lg p-0.5 border border-[var(--border-color)] mr-2">
          <button
            onClick={() => setShowLeftPanel(!showLeftPanel)}
            className={clsx("p-1.5 rounded-md hover:bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors", !showLeftPanel && "opacity-40")}
            title="Toggle Explorer"
          >
            <Sidebar size={16} />
          </button>
          <div className="w-[1px] bg-[var(--border-color)] mx-0.5 my-1" />
          <button
            onClick={() => setShowCenterPanel(!showCenterPanel)}
            className={clsx("p-1.5 rounded-md hover:bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors", !showCenterPanel && "opacity-40")}
            title="Toggle Editor"
          >
            <Layout size={16} />
          </button>
          <button
            onClick={toggleSplitMode}
            className={clsx(
              "p-1.5 rounded-md hover:bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors",
              isSplitMode && "bg-[var(--accent-light)] text-[var(--accent-primary)]"
            )}
            title={isSplitMode ? "Close split editor" : "Split editor"}
          >
            <Columns size={16} />
          </button>
          <div className="w-[1px] bg-[var(--border-color)] mx-0.5 my-1" />
          <button
            onClick={() => setShowRightPanel(!showRightPanel)}
            className={clsx("p-1.5 rounded-md hover:bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors", !showRightPanel && "opacity-40")}
            title="Toggle Chat"
          >
            <PanelRight size={16} />
          </button>
        </div>

        <button
          onClick={() => setShowThemeCustomizer(true)}
          className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          title="Theme Settings"
        >
          <Settings size={18} />
        </button>
      </div>
    </div>
  );

  // --- Layout ---
  // CRITICAL: Chat must be rendered in a STABLE position in the React tree to preserve state.
  // We achieve this by always rendering the same structure, using CSS to show/hide elements.
  // This prevents Chat from unmounting when switching between mobile/desktop layouts.

  return (
    <div className="h-[100dvh] flex flex-col bg-[var(--bg-primary)] text-[var(--text-primary)] overflow-hidden font-sans selection:bg-[var(--accent-primary)]/20 selection:text-[var(--text-primary)]">
      {/* Desktop Header - hidden on mobile */}
      <div className={clsx(!isMobile ? "block" : "hidden")}>
        <Header />
      </div>

      <div ref={containerRef} className="flex-1 overflow-hidden flex relative">
        {/* Mobile: Absolute positioned tabs for Files and Editor */}
        {isMobile && (
          <>
            <div className={clsx("absolute inset-0 transition-opacity duration-200 z-10 flex flex-col", activeTab === 'files' ? "opacity-100" : "opacity-0 pointer-events-none")}>
              <div className="flex-1 overflow-auto min-h-0">
                <FileTree
                  files={files}
                  onSelect={handleFileSelect}
                  selectedPath={selectedFile}
                  onCreateFile={handleCreateFile}
                  onCreateFolder={handleCreateFolder}
                  onDelete={handleDelete}
                  onRename={handleRename}
                  onCopyPath={handleCopyPath}
                  onRefresh={refreshFiles}
                  onSearch={() => setShowFileSearch(true)}
                  unsavedPaths={unsavedPaths}
                  onUploadFiles={handleUploadFiles}
                  onMoveFile={handleMoveFile}
                />
              </div>
              <AppDrawer onSelectApp={(entryPath) => {
                const fullPath = `05_App_Data/${entryPath}`;
                handleFileSelect(fullPath);
                openAppFullscreen(fullPath);
              }} />
            </div>
            <div className={clsx("absolute inset-0 transition-opacity duration-200 z-10", activeTab === 'editor' ? "opacity-100" : "opacity-0 pointer-events-none")}>
              <EditorView
                selectedFile={selectedFile}
                viewMode={viewMode}
                setViewMode={setViewMode}
                markdown={markdown}
                setMarkdown={setMarkdown}
                handleSave={handleSave}
                handleRevert={handleRevert}
                hasUnsavedChanges={selectedFile ? unsavedPaths.has(selectedFile) : false}
                onNavigateToFile={handleFileSelect}
                files={files}
                openTabs={openTabs}
                onTabSelect={handleFileSelect}
                onTabClose={handleCloseTab}
                unsavedPaths={unsavedPaths}
              />
            </div>
          </>
        )}

        {/* Desktop: Resizable panels for Files and Editor */}
        {!isMobile && (showLeftPanel || showCenterPanel) && (
          <PanelGroup direction="horizontal" className="flex-1">
            {showLeftPanel && (
              <>
                <Panel defaultSize={25} minSize={15} maxSize={40} className="bg-[var(--bg-primary)]">
                  <div className="flex flex-col h-full">
                    <div className="flex-1 overflow-auto min-h-0">
                      <FileTree
                        files={files}
                        onSelect={handleFileSelectRouted}
                        selectedPath={activePaneId === 'secondary' && isSplitMode ? secondaryFile : selectedFile}
                        onCreateFile={handleCreateFile}
                        onCreateFolder={handleCreateFolder}
                        onDelete={handleDelete}
                        onRename={handleRename}
                        onCopyPath={handleCopyPath}
                        onRefresh={refreshFiles}
                        onSearch={() => setShowFileSearch(true)}
                        unsavedPaths={unsavedPaths}
                        onUploadFiles={handleUploadFiles}
                        onMoveFile={handleMoveFile}
                      />
                    </div>
                    <AppDrawer onSelectApp={(entryPath) => {
                      const fullPath = `05_App_Data/${entryPath}`;
                      handleFileSelectRouted(fullPath);
                      openAppFullscreen(fullPath);
                    }} />
                  </div>
                </Panel>
                <PanelResizeHandle className="w-2 bg-[var(--border-color)] hover:bg-[var(--accent-primary)] active:bg-[var(--accent-primary)] transition-colors cursor-col-resize flex items-center justify-center group">
                    <div className="w-1 h-8 rounded-full bg-[var(--text-muted)] group-hover:bg-white transition-colors" />
                  </PanelResizeHandle>
              </>
            )}
            {showCenterPanel && (
              <Panel defaultSize={75} minSize={30} className="bg-[var(--bg-secondary)]">
                {isSplitMode ? (
                  <PanelGroup direction="horizontal">
                    {/* Primary pane */}
                    <Panel defaultSize={50} minSize={25}>
                      <EditorView
                        selectedFile={selectedFile}
                        viewMode={viewMode}
                        setViewMode={setViewMode}
                        markdown={markdown}
                        setMarkdown={setMarkdown}
                        handleSave={handleSave}
                        handleRevert={handleRevert}
                        hasUnsavedChanges={selectedFile ? unsavedPaths.has(selectedFile) : false}
                        onNavigateToFile={(path) => { setActivePaneId('primary'); handleFileSelect(path); }}
                        files={files}
                        openTabs={openTabs}
                        onTabSelect={(path) => { setActivePaneId('primary'); handleFileSelect(path); }}
                        onTabClose={handleCloseTab}
                        unsavedPaths={unsavedPaths}
                        isActive={activePaneId === 'primary'}
                        onPaneFocus={() => setActivePaneId('primary')}
                        onCloseSplit={toggleSplitMode}
                      />
                    </Panel>
                    <PanelResizeHandle className="w-1.5 bg-[var(--border-color)] hover:bg-[var(--accent-primary)] active:bg-[var(--accent-primary)] transition-colors cursor-col-resize flex items-center justify-center group">
                      <div className="w-0.5 h-6 rounded-full bg-[var(--text-muted)] group-hover:bg-white transition-colors" />
                    </PanelResizeHandle>
                    {/* Secondary pane */}
                    <Panel defaultSize={50} minSize={25}>
                      <EditorView
                        selectedFile={secondaryFile}
                        viewMode={secondaryViewMode}
                        setViewMode={setSecondaryViewMode}
                        markdown={secondaryMarkdown}
                        setMarkdown={setSecondaryMarkdown}
                        handleSave={handleSecondarySave}
                        handleRevert={handleSecondaryRevert}
                        hasUnsavedChanges={secondaryFile ? unsavedPaths.has(secondaryFile) : false}
                        onNavigateToFile={(path) => { setActivePaneId('secondary'); handleSecondaryFileSelect(path); }}
                        files={files}
                        openTabs={secondaryOpenTabs}
                        onTabSelect={(path) => { setActivePaneId('secondary'); handleSecondaryFileSelect(path); }}
                        onTabClose={handleSecondaryCloseTab}
                        unsavedPaths={unsavedPaths}
                        isActive={activePaneId === 'secondary'}
                        onPaneFocus={() => setActivePaneId('secondary')}
                        onCloseSplit={toggleSplitMode}
                      />
                    </Panel>
                  </PanelGroup>
                ) : (
                  <EditorView
                    selectedFile={selectedFile}
                    viewMode={viewMode}
                    setViewMode={setViewMode}
                    markdown={markdown}
                    setMarkdown={setMarkdown}
                    handleSave={handleSave}
                    handleRevert={handleRevert}
                    hasUnsavedChanges={selectedFile ? unsavedPaths.has(selectedFile) : false}
                    onNavigateToFile={handleFileSelect}
                    files={files}
                    openTabs={openTabs}
                    onTabSelect={handleFileSelect}
                    onTabClose={handleCloseTab}
                    unsavedPaths={unsavedPaths}
                  />
                )}
              </Panel>
            )}
          </PanelGroup>
        )}

        {/* Chat resize handle - only show when there's something to resize against */}
        {!isMobile && showRightPanel && (showLeftPanel || showCenterPanel) && (
          <div
            onMouseDown={startChatResize}
            className="w-2 bg-[var(--border-color)] hover:bg-[var(--accent-primary)] active:bg-[var(--accent-primary)] transition-colors cursor-col-resize flex items-center justify-center group flex-shrink-0"
          >
            <div className="w-1 h-8 rounded-full bg-[var(--text-muted)] group-hover:bg-white transition-colors" />
          </div>
        )}

        {/* Chat - ALWAYS RENDERED at this tree position to preserve state */}
        {/* When other panels are hidden, Chat expands to fill the space */}
        <div className={clsx(
          isMobile
            ? clsx("absolute inset-0 transition-opacity duration-200 z-10", activeTab === 'chat' ? "opacity-100" : "opacity-0 pointer-events-none")
            : clsx(
                showRightPanel ? "min-w-[250px]" : "hidden",
                // Fill space when no other panels are open
                showRightPanel && !showLeftPanel && !showCenterPanel && "flex-1"
              )
        )}
        style={!isMobile && showRightPanel && (showLeftPanel || showCenterPanel)
          ? { width: `${chatWidth}%`, flexShrink: 0 }
          : undefined
        }
        >
          {/* Split chat: two panels side by side */}
          {isChatSplit && !isMobile ? (
            <PanelGroup direction="horizontal" id="chat-split" className="h-full">
              <Panel defaultSize={50} minSize={30}>
                <div
                  className="h-full"
                  style={{ borderTop: activeChatPanel === 'primary' ? '2px solid var(--accent-primary)' : '2px solid transparent' }}
                  onMouseDown={() => setActiveChatPanel('primary')}
                >
                  <Chat
                    isMobile={isMobile}
                    onOpenFile={handleFileSelectRouted}
                    panelId="primary"
                    onSplitChat={handleSplitChat}
                    onPopoutChat={handlePopoutChat}
                  />
                </div>
              </Panel>
              <PanelResizeHandle className="w-1.5 bg-[var(--border-color)] hover:bg-[var(--accent-primary)] transition-colors cursor-col-resize" />
              <Panel defaultSize={50} minSize={30}>
                <div
                  className="h-full"
                  style={{ borderTop: activeChatPanel === 'secondary' ? '2px solid var(--accent-primary)' : '2px solid transparent' }}
                  onMouseDown={() => setActiveChatPanel('secondary')}
                >
                  <Chat
                    isMobile={isMobile}
                    onOpenFile={handleFileSelectRouted}
                    claudeHook={secondaryClaude}
                    panelId="secondary"
                    isSecondary
                    onCloseSplit={() => setIsChatSplit(false)}
                    onSplitChat={handleSplitChat}
                    onPopoutChat={handlePopoutChat}
                  />
                </div>
              </Panel>
            </PanelGroup>
          ) : (
            <Chat
              isMobile={isMobile}
              onOpenFile={handleFileSelectRouted}
              panelId="primary"
              onSplitChat={handleSplitChat}
              onPopoutChat={handlePopoutChat}
            />
          )}
        </div>
      </div>

      {/* Mobile Tab Bar - hidden on desktop */}
      <div className={clsx(isMobile ? "block" : "hidden")}>
        <div className="h-14 border-t border-[var(--border-color)] bg-[var(--bg-secondary)] flex justify-around items-center pb-safe z-50 shadow-warm">
          <button onClick={() => setActiveTab('files')} className="flex-1 p-2 flex flex-col items-center justify-center transition-colors" style={{ color: activeTab === 'files' ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
            <Menu size={20} strokeWidth={activeTab === 'files' ? 2.5 : 2} />
            <span className="text-[10px] mt-1 font-medium">Files</span>
          </button>
          <button onClick={() => setActiveTab('editor')} className="flex-1 p-2 flex flex-col items-center justify-center transition-colors" style={{ color: activeTab === 'editor' ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
            <FileText size={20} strokeWidth={activeTab === 'editor' ? 2.5 : 2} />
            <span className="text-[10px] mt-1 font-medium">Editor</span>
          </button>
          <button onClick={() => setActiveTab('chat')} className="flex-1 p-2 flex flex-col items-center justify-center transition-colors" style={{ color: activeTab === 'chat' ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
            <MessageSquare size={20} strokeWidth={activeTab === 'chat' ? 2.5 : 2} />
            <span className="text-[10px] mt-1 font-medium">Chat</span>
          </button>
          <button onClick={() => setShowThemeCustomizer(true)} className="flex-1 p-2 flex flex-col items-center justify-center transition-colors" style={{ color: showThemeCustomizer ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
            <Settings size={20} strokeWidth={showThemeCustomizer ? 2.5 : 2} />
            <span className="text-[10px] mt-1 font-medium">Settings</span>
          </button>
        </div>
      </div>

      {/* Hidden file input for uploads */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileInputChange}
      />

      {/* Modals */}
      <InputModal
        isOpen={inputModal.isOpen}
        title={inputModal.title}
        placeholder={inputModal.placeholder}
        initialValue={inputModal.initialValue}
        submitLabel={inputModal.submitLabel}
        onSubmit={inputModal.onSubmit}
        onCancel={closeInputModal}
      />
      <ConfirmModal
        isOpen={confirmModal.isOpen}
        title={confirmModal.title}
        message={confirmModal.message}
        confirmLabel={confirmModal.confirmLabel}
        destructive={true}
        onConfirm={confirmModal.onConfirm}
        onCancel={closeConfirmModal}
      />
      <FileSearchModal
        isOpen={showFileSearch}
        files={files}
        onSelect={(path) => {
          setShowFileSearch(false);
          handleFileSelectRouted(path);
        }}
        onClose={() => setShowFileSearch(false)}
      />
      <SettingsModal
        isOpen={showThemeCustomizer}
        onClose={() => setShowThemeCustomizer(false)}
        onExclusionsChanged={refreshFiles}
        onDefaultEditorFileChanged={(filePath) => {
          if (filePath) {
            handleFileSelect(filePath);
          }
        }}
        files={files}
      />

      {/* Full-screen App Mode */}
      {fullscreenApp && (
        <div className="fixed inset-0 z-50 flex flex-col bg-[var(--bg-primary)]">
          {/* App Header Bar */}
          <div className="h-10 px-3 border-b border-[var(--border-color)] flex items-center justify-between bg-[var(--bg-secondary)] shrink-0">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setFullscreenApp(null)}
                className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                title="Back to Second Brain (Esc)"
              >
                <ArrowLeft size={18} />
              </button>
              <span className="text-sm font-semibold text-[var(--text-primary)] select-none">{fullscreenApp.name}</span>
            </div>
            <button
              onClick={() => {
                setFullscreenApp(null);
                setShowRightPanel(true);
              }}
              className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
              title="Chat with Claude"
            >
              <MessageSquare size={18} />
            </button>
          </div>
          {/* App Content */}
          <div className="flex-1 overflow-hidden">
            <HtmlIframe html={fullscreenApp.html} />
          </div>
        </div>
      )}
    </div>
  );
}

export default App;