import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export interface MenuItem {
  label: string;
  icon?: React.ReactNode;
  shortcut?: string;
  onClick: () => void;
  destructive?: boolean;
}

export interface MenuSeparator {
  type: 'separator';
}

export type MenuItemOrSeparator = MenuItem | MenuSeparator;

interface ContextMenuProps {
  isOpen: boolean;
  x: number;
  y: number;
  items: MenuItemOrSeparator[];
  onClose: () => void;
}

function isSeparator(item: MenuItemOrSeparator): item is MenuSeparator {
  return 'type' in item && item.type === 'separator';
}

export const ContextMenu: React.FC<ContextMenuProps> = ({
  isOpen,
  x,
  y,
  items,
  onClose,
}) => {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ x, y });

  // Adjust position to stay within viewport
  useEffect(() => {
    if (!isOpen || !menuRef.current) return;

    const menuRect = menuRef.current.getBoundingClientRect();
    const padding = 10;

    let adjustedX = x;
    let adjustedY = y;

    // Adjust horizontal position
    if (x + menuRect.width + padding > window.innerWidth) {
      adjustedX = Math.max(padding, window.innerWidth - menuRect.width - padding);
    }

    // Adjust vertical position
    if (y + menuRect.height + padding > window.innerHeight) {
      adjustedY = Math.max(padding, window.innerHeight - menuRect.height - padding);
    }

    setPosition({ x: adjustedX, y: adjustedY });
  }, [isOpen, x, y]);

  // Close on click outside or escape
  useEffect(() => {
    if (!isOpen) return;

    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    // Use capture phase and slight delay to avoid immediate close
    const timeoutId = setTimeout(() => {
      document.addEventListener('click', handleClick, true);
    }, 0);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      clearTimeout(timeoutId);
      document.removeEventListener('click', handleClick, true);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-50 min-w-[160px] bg-[var(--bg-secondary)] rounded-lg shadow-lg border border-[var(--border-color)] py-1 animate-context-menu"
      style={{ left: position.x, top: position.y }}
    >
      {items.map((item, index) => {
        if (isSeparator(item)) {
          return (
            <div
              key={`sep-${index}`}
              className="h-px bg-[var(--border-color)] my-1 mx-2"
            />
          );
        }

        return (
          <button
            key={item.label}
            onClick={() => {
              item.onClick();
              onClose();
            }}
            className={`w-full px-3 py-1.5 text-sm text-left flex items-center gap-2 transition-colors ${
              item.destructive
                ? 'text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20'
                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]'
            }`}
          >
            {item.icon && (
              <span className="w-4 h-4 flex items-center justify-center opacity-70">
                {item.icon}
              </span>
            )}
            <span className="flex-1">{item.label}</span>
            {item.shortcut && (
              <span className="text-xs text-[var(--text-muted)] ml-4">{item.shortcut}</span>
            )}
          </button>
        );
      })}
    </div>,
    document.body
  );
};
