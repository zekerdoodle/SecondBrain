import React, { useState, useMemo } from 'react';
import { ChevronRight, ChevronDown, FileCode, Folder, FolderOpen, FileText, Image, File as FileIcon, Plus, FolderPlus, MoreHorizontal, Edit3, Trash2, Copy, RefreshCw } from 'lucide-react';
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
  unsavedPaths?: Set<string>;
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
}

const TreeNode: React.FC<TreeNodeProps> = ({ node, onSelect, selectedPath, depth = 0, onContextMenu, unsavedPaths }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const isSelected = node.path === selectedPath;
  const hasUnsavedChanges = unsavedPaths?.has(node.path) ?? false;

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
    e.dataTransfer.effectAllowed = 'copy';
  };

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

  return (
    <div>
      <div
        draggable
        onDragStart={handleDragStart}
        onContextMenu={handleContextMenu}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        className={clsx(
          "flex items-center py-1.5 px-2 cursor-pointer transition-colors text-sm select-none border-l-2 group",
          isSelected ? "bg-[var(--accent-primary)]/10 border-[var(--accent-primary)] text-[var(--text-primary)]" : "border-transparent text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]"
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
  unsavedPaths,
}) => {
  const tree = useMemo(() => buildTree(files), [files]);

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
        </div>
      </div>
      <div
        className="flex-1 overflow-y-auto py-2"
        onContextMenu={handleBackgroundContextMenu}
      >
        {tree.map(node => (
          <TreeNode
            key={node.path}
            node={node}
            onSelect={onSelect}
            selectedPath={selectedPath}
            onContextMenu={handleNodeContextMenu}
            unsavedPaths={unsavedPaths}
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
