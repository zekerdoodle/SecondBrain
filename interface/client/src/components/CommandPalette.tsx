import React, { useEffect, useRef, useCallback } from 'react';
import { clsx } from 'clsx';
import { Command as CommandIcon, ArrowRight } from 'lucide-react';
import { type Command, searchCommands } from '../utils/commands';

interface CommandPaletteProps {
  /** The current query (text after /) */
  query: string;
  /** Called when a command is selected */
  onSelect: (command: Command) => void;
  /** Called when palette should close */
  onClose: () => void;
  /** Currently selected index */
  selectedIndex: number;
  /** Called when selection changes */
  onSelectionChange: (index: number) => void;
}

/**
 * Highlight matching characters in a string
 */
function HighlightedText({ text, indices }: { text: string; indices: number[] }) {
  if (indices.length === 0) {
    return <span>{text}</span>;
  }

  const indicesSet = new Set(indices);
  return (
    <span>
      {text.split('').map((char, i) => (
        <span
          key={i}
          className={indicesSet.has(i) ? 'text-[var(--accent-primary)] font-semibold' : ''}
        >
          {char}
        </span>
      ))}
    </span>
  );
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({
  query,
  onSelect,
  onClose,
  selectedIndex,
  onSelectionChange,
}) => {
  const listRef = useRef<HTMLDivElement>(null);
  const results = searchCommands(query);

  // Ensure selected index is within bounds
  useEffect(() => {
    if (selectedIndex >= results.length) {
      onSelectionChange(Math.max(0, results.length - 1));
    }
  }, [results.length, selectedIndex, onSelectionChange]);

  // Scroll selected item into view
  useEffect(() => {
    if (listRef.current) {
      const selected = listRef.current.querySelector('[data-selected="true"]');
      if (selected) {
        selected.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [selectedIndex]);

  // Handle keyboard navigation
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        onSelectionChange((selectedIndex + 1) % results.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        onSelectionChange((selectedIndex - 1 + results.length) % results.length);
        break;
      case 'Enter':
        e.preventDefault();
        if (results[selectedIndex]) {
          onSelect(results[selectedIndex]);
        }
        break;
      case 'Escape':
        e.preventDefault();
        onClose();
        break;
      case 'Tab':
        e.preventDefault();
        if (results[selectedIndex]) {
          onSelect(results[selectedIndex]);
        }
        break;
    }
  }, [selectedIndex, results, onSelect, onClose, onSelectionChange]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  if (results.length === 0) {
    return (
      <div className="absolute bottom-full left-0 right-0 mb-2 bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-lg overflow-hidden z-50">
        <div className="p-4 text-center text-[var(--text-muted)] text-sm">
          No commands found
        </div>
      </div>
    );
  }

  return (
    <div
      className="absolute bottom-full left-0 right-0 mb-2 bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-lg overflow-hidden z-50 max-h-[300px] command-palette-in"
      role="listbox"
    >
      {/* Header */}
      <div className="px-3 py-2 border-b border-[var(--border-color)] bg-[var(--bg-tertiary)]">
        <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
          <CommandIcon size={12} />
          <span>Commands</span>
          <span className="ml-auto opacity-60">↑↓ navigate · Enter select · Esc close</span>
        </div>
      </div>

      {/* Command list */}
      <div ref={listRef} className="overflow-y-auto max-h-[240px]">
        {results.map((cmd, idx) => {
          const isSelected = idx === selectedIndex;
          return (
            <div
              key={cmd.name}
              role="option"
              aria-selected={isSelected}
              data-selected={isSelected}
              onClick={() => onSelect(cmd)}
              onMouseEnter={() => onSelectionChange(idx)}
              className={clsx(
                'flex items-center gap-3 px-3 py-2.5 cursor-pointer transition-colors',
                isSelected
                  ? 'bg-[var(--accent-primary)]/10'
                  : 'hover:bg-[var(--bg-tertiary)]'
              )}
            >
              {/* Command icon/slash */}
              <div className={clsx(
                'w-8 h-8 rounded-lg flex items-center justify-center text-sm font-mono shrink-0',
                isSelected
                  ? 'bg-[var(--accent-primary)] text-white'
                  : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]'
              )}>
                /
              </div>

              {/* Command details */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={clsx(
                    'font-medium',
                    isSelected ? 'text-[var(--accent-primary)]' : 'text-[var(--text-primary)]'
                  )}>
                    <HighlightedText text={cmd.name} indices={cmd.matchIndices} />
                  </span>
                  {cmd.hasArgs && (
                    <span className="text-xs text-[var(--text-muted)] opacity-60">
                      {cmd.argsPlaceholder || 'args'}
                    </span>
                  )}
                </div>
                <p className="text-xs text-[var(--text-muted)] truncate">
                  {cmd.description}
                </p>
              </div>

              {/* Arrow indicator when selected */}
              {isSelected && (
                <ArrowRight size={16} className="text-[var(--accent-primary)] shrink-0" />
              )}
            </div>
          );
        })}
      </div>

      {/* Hint for selected command */}
      {results[selectedIndex]?.hint && (
        <div className="px-3 py-2 border-t border-[var(--border-color)] bg-[var(--bg-tertiary)]">
          <p className="text-xs text-[var(--text-muted)]">
            {results[selectedIndex].hint}
          </p>
        </div>
      )}
    </div>
  );
};
