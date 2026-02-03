import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { FileTree } from './FileTree';
import { Chat } from './Chat';
import { EditorView } from './Editor';
import { InputModal } from './components/InputModal';
import { ConfirmModal } from './components/ConfirmModal';
import { SettingsModal, useThemeInit } from './components/SettingsModal';
import { Menu, FileText, MessageSquare, Sidebar, PanelRight, Settings, Layout } from 'lucide-react';
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
  // Track the "clean" content (last saved/loaded) to detect changes
  const [savedContent, setSavedContent] = useState<string>('');
  // Theme customizer modal
  const [showThemeCustomizer, setShowThemeCustomizer] = useState(false);

  // Visibility Toggles
  const [showLeftPanel, setShowLeftPanel] = useState(true);
  const [showCenterPanel, setShowCenterPanel] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(true);

  // Editor Mode: 'view' (formatted) or 'edit' (raw text)
  const [viewMode, setViewMode] = useState<'view' | 'edit'>('view');

  const [activeTab, setActiveTab] = useState<'files' | 'editor' | 'chat'>('files');

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

  // Compute which files have unsaved changes
  const unsavedPaths = useMemo(() => {
    const paths = new Set(Object.keys(draftContent));
    // Also include current file if it has unsaved changes
    if (selectedFile && markdown !== savedContent) {
      paths.add(selectedFile);
    }
    return paths;
  }, [draftContent, selectedFile, markdown, savedContent]);

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
            }
          }
        }
      } catch (e) {
        console.error('Failed to load default editor file:', e);
      }
    };

    loadInitialFile();
  }, []);

  const handleFileSelect = async (path: string) => {
    // Save current file's draft if it has unsaved changes
    if (selectedFile && markdown !== savedContent) {
      setDraftContent(prev => ({ ...prev, [selectedFile]: markdown }));
    }

    setSelectedFile(path);

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
        // Clean up draft for deleted file
        setDraftContent(prev => {
          const next = { ...prev };
          delete next[path];
          return next;
        });
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
            <div className={clsx("absolute inset-0 transition-opacity duration-200 z-10", activeTab === 'files' ? "opacity-100" : "opacity-0 pointer-events-none")}>
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
                unsavedPaths={unsavedPaths}
              />
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
                    unsavedPaths={unsavedPaths}
                  />
                </Panel>
                <PanelResizeHandle className="w-2 bg-[var(--border-color)] hover:bg-[var(--accent-primary)] active:bg-[var(--accent-primary)] transition-colors cursor-col-resize flex items-center justify-center group">
                    <div className="w-1 h-8 rounded-full bg-[var(--text-muted)] group-hover:bg-white transition-colors" />
                  </PanelResizeHandle>
              </>
            )}
            {showCenterPanel && (
              <Panel defaultSize={75} minSize={30} className="bg-[var(--bg-secondary)]">
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
                />
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
          <Chat isMobile={isMobile} />
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
            <span className="text-[10px] mt-1 font-medium">Claude</span>
          </button>
          <button onClick={() => setShowThemeCustomizer(true)} className="flex-1 p-2 flex flex-col items-center justify-center transition-colors" style={{ color: showThemeCustomizer ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
            <Settings size={20} strokeWidth={showThemeCustomizer ? 2.5 : 2} />
            <span className="text-[10px] mt-1 font-medium">Settings</span>
          </button>
        </div>
      </div>

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
    </div>
  );
}

export default App;