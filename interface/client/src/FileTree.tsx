import React, { useState, useEffect, useMemo, useRef } from 'react';
import { ChevronRight, ChevronDown, FileCode, Folder, FolderOpen, FileText, Image, File as FileIcon, Plus, FolderPlus, MoreHorizontal, Edit3, Trash2, Copy, RefreshCw, Upload, Search } from 'lucide-react';
import { clsx } from 'clsx';
import { ContextMenu, type MenuItemOrSeparator } from './components/ContextMenu';

interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'folder';
  children?: FileNode[];
}

interface FileTreeProps {
  files: string[];
  onSelect: (path: string) => void;
  selectedPath?: string;
  onCreateFile: (parentPath?: string) => void;
  onCreateFolder: (parentPath?: string) => void;
  onDelete: (path: string) => void;
  onRename: (path: string) => void;
  onCopyPath: (path: string) => void;
  onRefresh: () => void;
  onSearch?: () => void;
  unsavedPaths?: Set<string>;
  onUploadFiles?: (parentPath?: string, files?: FileList | File[]) => void;
  onMoveFile?: (sourcePath: string, destinationFolder: string) => Promise<boolean>;
}

interface ContextMenuState {
  isOpen: boolean;
  x: number;
  y: number;
  node: FileNode | null; // null means background (empty area)
}

const buildTree = (paths: string[]): FileNode[] => {
  const root: FileNode[] = [];
  paths.forEach(path => {
    const parts = path.split('/');
    let currentLevel = root;
    parts.forEach((part, index) => {
      const isFile = index === parts.length - 1;
      const existingNode = currentLevel.find(n => n.name === part);
      if (existingNode) {
        if (!isFile && !existingNode.children) existingNode.children = [];
        currentLevel = existingNode.children!;
      } else {
        const newNode: FileNode = {
          name: part,
          path: parts.slice(0, index + 1).join('/'),
          type: isFile ? 'file' : 'folder',
          children: isFile ? undefined : []
        };
        currentLevel.push(newNode);
        if (!isFile) currentLevel = newNode.children!;
      }
    });
  });
  // Sort: Folders first, then files, alphabetical
  const sortNodes = (nodes: FileNode[]) => {
      nodes.sort((a, b) => {
          if (a.type === b.type) return a.name.localeCompare(b.name);
          return a.type === 'folder' ? -1 : 1;
      });
      nodes.forEach(n => { if (n.children) sortNodes(n.children); });
  };
  sortNodes(root);
  return root;
};

const getIcon = (name: string, type: 'file' | 'folder', isOpen: boolean) => {
    if (type === 'folder') return isOpen ? <FolderOpen size={14} style={{ color: 'var(--accent-primary)' }} /> : <Folder size={14} style={{ color: 'var(--accent-primary)' }} />;
    if (name.endsWith('.md')) return <FileText size={14} className="text-[var(--text-secondary)]" />;
    if (name.endsWith('.py') || name.endsWith('.js') || name.endsWith('.ts') || name.endsWith('.tsx')) return <FileCode size={14} className="text-amber-500" />;
    if (name.endsWith('.png') || name.endsWith('.jpg')) return <Image size={14} className="text-purple-400" />;
    return <FileIcon size={14} className="text-[var(--text-muted)]" />;
};

interface TreeNodeProps {
  node: FileNode;
  onSelect: (p: string) => void;
  selectedPath?: string;
  depth?: number;
  onContextMenu: (e: React.MouseEvent, node: FileNode) => void;
  unsavedPaths?: Set<string>;
  onMoveFile?: (sourcePath: string, destinationFolder: string) => Promise<boolean>;
  draggedPath: string | null;
  setDraggedPath: (path: string | null) => void;
}

const TreeNode: React.FC<TreeNodeProps> = ({ node, onSelect, selectedPath, depth = 0, onContextMenu, unsavedPaths, onMoveFile, draggedPath, setDraggedPath }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const dragOverCounter = useRef(0);
  const expandTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isSelected = node.path === selectedPath;

  // Auto-expand folder when selected file is a descendant
  useEffect(() => {
    if (node.type === 'folder' && selectedPath && selectedPath.startsWith(node.path + '/')) {
      setIsOpen(true);
    }
  }, [selectedPath, node.path, node.type]);
  const hasUnsavedChanges = unsavedPaths?.has(node.path) ?? false;

  // Is this a valid drop target? Can't drop onto self, or into own children
  const isValidDropTarget = node.type === 'folder' && draggedPath !== null
    && draggedPath !== node.path
    && !node.path.startsWith(draggedPath + '/')
    // Don't allow dropping into the same parent folder (would be a no-op)
    && draggedPath.split('/').slice(0, -1).join('/') !== node.path;

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (node.type === 'folder') {
      setIsOpen(!isOpen);
    } else {
      onSelect(node.path);
    }
  };

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('text/plain', node.path);
    e.dataTransfer.setData('application/x-secondbrain-file', JSON.stringify({
      name: node.name,
      path: node.path,
      type: node.type
    }));
    e.dataTransfer.effectAllowed = 'copyMove';
    setDraggedPath(node.path);
  };

  const handleDragEnd = () => {
    setDraggedPath(null);
  };

  const handleDragOver = (e: React.DragEvent) => {
    if (!isValidDropTarget) return;
    // Check for internal drag (not OS file drop)
    if (e.dataTransfer.types.includes('application/x-secondbrain-file')) {
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    if (!isValidDropTarget) return;
    if (e.dataTransfer.types.includes('application/x-secondbrain-file')) {
      e.preventDefault();
      e.stopPropagation();
      dragOverCounter.current++;
      setIsDragOver(true);

      // Auto-expand collapsed folders after 500ms hover
      if (!isOpen && node.children) {
        expandTimeout.current = setTimeout(() => {
          setIsOpen(true);
        }, 500);
      }
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.stopPropagation();
    dragOverCounter.current--;
    if (dragOverCounter.current <= 0) {
      dragOverCounter.current = 0;
      setIsDragOver(false);
      if (expandTimeout.current) {
        clearTimeout(expandTimeout.current);
        expandTimeout.current = null;
      }
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragOverCounter.current = 0;
    setIsDragOver(false);
    if (expandTimeout.current) {
      clearTimeout(expandTimeout.current);
      expandTimeout.current = null;
    }

    const data = e.dataTransfer.getData('application/x-secondbrain-file');
    if (data && onMoveFile && node.type === 'folder') {
      try {
        const { path: sourcePath } = JSON.parse(data);
        if (sourcePath && sourcePath !== node.path && !node.path.startsWith(sourcePath + '/')) {
          await onMoveFile(sourcePath, node.path);
        }
      } catch (err) {
        console.error('Drop failed:', err);
      }
    }
  };

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (expandTimeout.current) clearTimeout(expandTimeout.current);
    };
  }, []);

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onContextMenu(e, node);
  };

  const handleMoreClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Get button position for menu placement
    const rect = e.currentTarget.getBoundingClientRect();
    const syntheticEvent = {
      ...e,
      clientX: rect.left,
      clientY: rect.bottom,
    } as React.MouseEvent;
    onContextMenu(syntheticEvent, node);
  };

  // Dim the item being dragged
  const isBeingDragged = draggedPath === node.path;

  return (
    <div>
      <div
        draggable
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragOver={handleDragOver}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onContextMenu={handleContextMenu}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        className={clsx(
          "flex items-center py-1.5 px-2 cursor-pointer transition-colors text-sm select-none border-l-2 group",
          isSelected ? "bg-[var(--accent-primary)]/10 border-[var(--accent-primary)] text-[var(--text-primary)]" : "border-transparent text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]",
          isDragOver && isValidDropTarget && "!bg-[var(--accent-primary)]/20 !border-[var(--accent-primary)] ring-1 ring-inset ring-[var(--accent-primary)]/50",
          isBeingDragged && "opacity-40"
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleClick}
      >
        <span className="mr-2 opacity-70 shrink-0 text-[var(--text-muted)]">
          {node.type === 'folder' && (
             isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />
          )}
           {node.type === 'file' && <span className="w-3 inline-block" />}
        </span>
        <span className="mr-2 shrink-0">{getIcon(node.name, node.type, isOpen)}</span>
        <span className="truncate font-medium flex-1">{node.name}</span>
        {hasUnsavedChanges && (
          <span className="w-2 h-2 rounded-full bg-[var(--accent-primary)] shrink-0 mr-1" title="Unsaved changes" />
        )}
        {/* Three-dots menu button - visible on hover */}
        <button
          onClick={handleMoreClick}
          className={clsx(
            "p-0.5 rounded hover:bg-[var(--border-color)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-opacity shrink-0",
            isHovered ? "opacity-100" : "opacity-0"
          )}
        >
          <MoreHorizontal size={14} />
        </button>
      </div>
      {isOpen && node.children && (
        <div className="border-l border-[var(--border-color)] ml-[15px]">
          {node.children.map(child => (
            <TreeNode
              key={child.path}
              node={child}
              onSelect={onSelect}
              selectedPath={selectedPath}
              depth={depth + 1}
              onContextMenu={onContextMenu}
              unsavedPaths={unsavedPaths}
              onMoveFile={onMoveFile}
              draggedPath={draggedPath}
              setDraggedPath={setDraggedPath}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export const FileTree: React.FC<FileTreeProps> = ({
  files,
  onSelect,
  selectedPath,
  onCreateFile,
  onCreateFolder,
  onDelete,
  onRename,
  onCopyPath,
  onRefresh,
  onSearch,
  unsavedPaths,
  onUploadFiles,
  onMoveFile,
}) => {
  const tree = useMemo(() => buildTree(files), [files]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isInternalDragOverRoot, setIsInternalDragOverRoot] = useState(false);
  const dragCounter = useRef(0);
  const internalDragCounter = useRef(0);
  // Track which item is being dragged (shared across all TreeNodes)
  const [draggedPath, setDraggedPath] = useState<string | null>(null);

  // Context menu state
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    isOpen: false,
    x: 0,
    y: 0,
    node: null,
  });

  const closeContextMenu = () => setContextMenu(prev => ({ ...prev, isOpen: false }));

  const handleNodeContextMenu = (e: React.MouseEvent, node: FileNode) => {
    setContextMenu({
      isOpen: true,
      x: e.clientX,
      y: e.clientY,
      node,
    });
  };

  const handleBackgroundContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setContextMenu({
      isOpen: true,
      x: e.clientX,
      y: e.clientY,
      node: null,
    });
  };

  // Build menu items based on context
  const getMenuItems = (): MenuItemOrSeparator[] => {
    const { node } = contextMenu;

    // Background (empty area) menu
    if (!node) {
      return [
        {
          label: 'New File',
          icon: <Plus size={14} />,
          onClick: () => onCreateFile(),
        },
        {
          label: 'New Folder',
          icon: <FolderPlus size={14} />,
          onClick: () => onCreateFolder(),
        },
        ...(onUploadFiles ? [
          { type: 'separator' as const },
          {
            label: 'Upload Files',
            icon: <Upload size={14} />,
            onClick: () => onUploadFiles(),
          },
        ] : []),
      ];
    }

    // Folder menu
    if (node.type === 'folder') {
      return [
        {
          label: 'New File',
          icon: <Plus size={14} />,
          onClick: () => onCreateFile(node.path),
        },
        {
          label: 'New Folder',
          icon: <FolderPlus size={14} />,
          onClick: () => onCreateFolder(node.path),
        },
        ...(onUploadFiles ? [{
          label: 'Upload Files',
          icon: <Upload size={14} />,
          onClick: () => onUploadFiles(node.path),
        }] : []),
        { type: 'separator' },
        {
          label: 'Rename',
          icon: <Edit3 size={14} />,
          shortcut: 'F2',
          onClick: () => onRename(node.path),
        },
        {
          label: 'Delete',
          icon: <Trash2 size={14} />,
          shortcut: 'Del',
          onClick: () => onDelete(node.path),
          destructive: true,
        },
        { type: 'separator' },
        {
          label: 'Copy Path',
          icon: <Copy size={14} />,
          onClick: () => onCopyPath(node.path),
        },
      ];
    }

    // File menu
    return [
      {
        label: 'Rename',
        icon: <Edit3 size={14} />,
        shortcut: 'F2',
        onClick: () => onRename(node.path),
      },
      {
        label: 'Delete',
        icon: <Trash2 size={14} />,
        shortcut: 'Del',
        onClick: () => onDelete(node.path),
        destructive: true,
      },
      { type: 'separator' },
      {
        label: 'Copy Path',
        icon: <Copy size={14} />,
        onClick: () => onCopyPath(node.path),
      },
    ];
  };

  return (
    <div className="h-full flex flex-col bg-[var(--bg-primary)] border-r border-[var(--border-color)]">
      <div className="p-3 border-b border-[var(--border-color)] bg-[var(--bg-secondary)] flex justify-between items-center">
        <span className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">Explorer</span>
        <div className="flex items-center gap-1">
          {onSearch && (
            <button
              onClick={onSearch}
              className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-md text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors"
              title="Search Files"
            >
              <Search size={14} />
            </button>
          )}
          <button
            onClick={onRefresh}
            className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-md text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={() => onCreateFile()}
            className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-md text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors"
            title="New File"
          >
            <Plus size={14} />
          </button>
          <button
            onClick={() => onCreateFolder()}
            className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-md text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors"
            title="New Folder"
          >
            <FolderPlus size={14} />
          </button>
          {onUploadFiles && (
            <button
              onClick={() => onUploadFiles()}
              className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-md text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors"
              title="Upload Files"
            >
              <Upload size={14} />
            </button>
          )}
        </div>
      </div>
      <div
        className={clsx(
          "flex-1 overflow-y-auto py-2 transition-colors",
          isDragOver && "bg-[var(--accent-primary)]/10 ring-2 ring-inset ring-[var(--accent-primary)]/30",
          isInternalDragOverRoot && !isDragOver && "bg-[var(--accent-primary)]/5 ring-2 ring-inset ring-[var(--accent-primary)]/20"
        )}
        onContextMenu={handleBackgroundContextMenu}
        onDragEnter={(e) => {
          // OS file drops
          if (e.dataTransfer.types.includes('Files')) {
            e.preventDefault();
            dragCounter.current++;
            setIsDragOver(true);
          }
          // Internal move: drop to root level
          if (e.dataTransfer.types.includes('application/x-secondbrain-file') && draggedPath) {
            e.preventDefault();
            internalDragCounter.current++;
            // Only highlight root if the item isn't already at root
            if (draggedPath.includes('/')) {
              setIsInternalDragOverRoot(true);
            }
          }
        }}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes('Files')) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
          }
          if (e.dataTransfer.types.includes('application/x-secondbrain-file') && draggedPath) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
          }
        }}
        onDragLeave={() => {
          dragCounter.current--;
          if (dragCounter.current <= 0) {
            dragCounter.current = 0;
            setIsDragOver(false);
          }
          internalDragCounter.current--;
          if (internalDragCounter.current <= 0) {
            internalDragCounter.current = 0;
            setIsInternalDragOverRoot(false);
          }
        }}
        onDrop={async (e) => {
          e.preventDefault();
          dragCounter.current = 0;
          setIsDragOver(false);
          internalDragCounter.current = 0;
          setIsInternalDragOverRoot(false);

          // OS file upload
          if (e.dataTransfer.files.length > 0 && onUploadFiles) {
            onUploadFiles(undefined, e.dataTransfer.files);
            return;
          }

          // Internal move to root
          const data = e.dataTransfer.getData('application/x-secondbrain-file');
          if (data && onMoveFile) {
            try {
              const { path: sourcePath } = JSON.parse(data);
              // Only move if source isn't already at root
              if (sourcePath && sourcePath.includes('/')) {
                await onMoveFile(sourcePath, '');
              }
            } catch (err) {
              console.error('Root drop failed:', err);
            }
          }
        }}
      >
        {tree.map(node => (
          <TreeNode
            key={node.path}
            node={node}
            onSelect={onSelect}
            selectedPath={selectedPath}
            onContextMenu={handleNodeContextMenu}
            unsavedPaths={unsavedPaths}
            onMoveFile={onMoveFile}
            draggedPath={draggedPath}
            setDraggedPath={setDraggedPath}
          />
        ))}
      </div>

      {/* Context Menu */}
      <ContextMenu
        isOpen={contextMenu.isOpen}
        x={contextMenu.x}
        y={contextMenu.y}
        items={getMenuItems()}
        onClose={closeContextMenu}
      />
    </div>
  );
};
