import React, { useState, useEffect, useRef, useCallback } from 'react';
import { clsx } from 'clsx';
import { Zap, HelpCircle, Compass, Wand2 } from 'lucide-react';

export interface SlashArg {
  name: string;
  type: string;
  description?: string;
  default?: any;
  enum?: string[];
  required?: boolean;
}

export interface SlashQuickPick {
  label: string;
  subtitle?: string;
  args: Record<string, any>;
}

export interface SlashCommand {
  name: string;
  description: string;
  args: SlashArg[];
  quick_picks?: SlashQuickPick[];
}

interface SlashCommandAutocompleteProps {
  input: string;
  commands: SlashCommand[];
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  onPickCommand: (command: string, args: Record<string, any>) => void;
  onInsertCommandName: (name: string) => void;
}

// Map command names to icons (use `any` since lucide types vary across versions)
const COMMAND_ICONS: Record<string, any> = {
  compact: Zap,
  help: HelpCircle,
};

const DEFAULT_ICON: any = Compass;

/**
 * Floating autocomplete dropdown for /slash commands in the chat input.
 *
 * Triggers when input STARTS with `/` (no chars before it). This is stricter
 * than @mention autocomplete because file paths and other text often contain
 * forward slashes.
 *
 * Two-stage UI:
 *   Stage 1 — Command picker: lists all commands matching the typed prefix.
 *             Arrow keys + Enter to insert just the name (e.g. `/compact `).
 *   Stage 2 — Quick-pick picker: once a known command name is typed, shows
 *             quick-pick options (e.g. modes for /compact). Enter on one
 *             dispatches the command immediately, no further typing needed.
 */
export const SlashCommandAutocomplete: React.FC<SlashCommandAutocompleteProps> = ({
  input,
  commands,
  textareaRef,
  onPickCommand,
  onInsertCommandName,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [stage, setStage] = useState<'commands' | 'quickpicks'>('commands');
  const [filter, setFilter] = useState('');
  const [matchedCommand, setMatchedCommand] = useState<SlashCommand | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Detect slash input
  useEffect(() => {
    if (!input.startsWith('/')) {
      setIsOpen(false);
      return;
    }

    const stripped = input.slice(1);
    // Tokens: ["compact"] or ["compact", "strip_tools"] etc.
    const firstSpace = stripped.indexOf(' ');

    if (firstSpace === -1) {
      // Still typing the command name
      setStage('commands');
      setFilter(stripped.toLowerCase());
      setMatchedCommand(null);
      setIsOpen(true);
      setSelectedIndex(0);
    } else {
      const commandName = stripped.slice(0, firstSpace).toLowerCase();
      const cmd = commands.find(c => c.name === commandName);
      if (cmd && cmd.quick_picks && cmd.quick_picks.length > 0) {
        setStage('quickpicks');
        setMatchedCommand(cmd);
        setIsOpen(true);
        setSelectedIndex(0);
        setFilter('');
      } else {
        setIsOpen(false);
      }
    }
  }, [input, commands]);

  const filteredCommands = commands.filter(c =>
    c.name.toLowerCase().startsWith(filter)
  );

  // Auto-close when no commands match
  useEffect(() => {
    if (stage === 'commands' && isOpen && filteredCommands.length === 0) {
      setIsOpen(false);
    }
  }, [stage, isOpen, filteredCommands.length]);

  // Keyboard handler
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (!isOpen) return;

    if (stage === 'commands') {
      if (filteredCommands.length === 0) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex(prev => (prev + 1) % filteredCommands.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex(prev => (prev - 1 + filteredCommands.length) % filteredCommands.length);
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        e.stopPropagation();
        const cmd = filteredCommands[selectedIndex];
        if (cmd) {
          // If the command has quick picks, insert "/name " and switch to quick-pick stage
          if (cmd.quick_picks && cmd.quick_picks.length > 0) {
            onInsertCommandName(cmd.name);
          } else if (cmd.args && cmd.args.length === 0) {
            // No args — dispatch immediately
            onPickCommand(cmd.name, {});
          } else {
            // Has args but no quick-picks — insert name and let user type
            onInsertCommandName(cmd.name);
          }
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setIsOpen(false);
      }
    } else if (stage === 'quickpicks' && matchedCommand) {
      const picks = matchedCommand.quick_picks || [];
      if (picks.length === 0) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex(prev => (prev + 1) % picks.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex(prev => (prev - 1 + picks.length) % picks.length);
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        e.stopPropagation();
        const pick = picks[selectedIndex];
        if (pick) {
          onPickCommand(matchedCommand.name, pick.args);
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setIsOpen(false);
      }
    }
  }, [isOpen, stage, filteredCommands, selectedIndex, matchedCommand, onPickCommand, onInsertCommandName]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.addEventListener('keydown', handleKeyDown, true);
    return () => textarea.removeEventListener('keydown', handleKeyDown, true);
  }, [textareaRef, handleKeyDown]);

  if (!isOpen) return null;

  // Stage 1: Command picker
  if (stage === 'commands') {
    if (filteredCommands.length === 0) return null;
    return (
      <div
        ref={dropdownRef}
        className="absolute bottom-full left-0 right-0 mb-2 bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-lg z-50 overflow-hidden max-h-72 overflow-y-auto"
      >
        <div className="px-3 py-1.5 text-[10px] font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border-color)] flex items-center justify-between">
          <span>Slash command</span>
          <span className="text-[10px] normal-case tracking-normal opacity-60">↑↓ navigate · ↵ select · esc cancel</span>
        </div>
        {filteredCommands.map((cmd, idx) => {
          const Icon = COMMAND_ICONS[cmd.name] || DEFAULT_ICON;
          return (
            <button
              key={cmd.name}
              onClick={() => {
                if (cmd.quick_picks && cmd.quick_picks.length > 0) {
                  onInsertCommandName(cmd.name);
                } else if (cmd.args && cmd.args.length === 0) {
                  onPickCommand(cmd.name, {});
                } else {
                  onInsertCommandName(cmd.name);
                }
              }}
              onMouseEnter={() => setSelectedIndex(idx)}
              className={clsx(
                "w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors",
                idx === selectedIndex
                  ? "bg-[var(--accent-light)]"
                  : "hover:bg-[var(--bg-tertiary)]"
              )}
            >
              <div
                className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
                style={{ backgroundColor: 'var(--accent-primary)' }}
              >
                <Icon size={14} className="text-white" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-[var(--text-primary)] truncate">
                  /{cmd.name}
                </div>
                <div className="text-xs text-[var(--text-muted)] truncate">
                  {cmd.description}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    );
  }

  // Stage 2: Quick-pick picker for the matched command
  if (stage === 'quickpicks' && matchedCommand) {
    const picks = matchedCommand.quick_picks || [];
    if (picks.length === 0) return null;
    return (
      <div
        ref={dropdownRef}
        className="absolute bottom-full left-0 right-0 mb-2 bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-lg z-50 overflow-hidden max-h-72 overflow-y-auto"
      >
        <div className="px-3 py-1.5 text-[10px] font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border-color)] flex items-center justify-between">
          <span>/{matchedCommand.name} · pick a mode</span>
          <span className="text-[10px] normal-case tracking-normal opacity-60">↑↓ · ↵ run · esc cancel</span>
        </div>
        {picks.map((pick, idx) => (
          <button
            key={`${pick.label}-${idx}`}
            onClick={() => onPickCommand(matchedCommand.name, pick.args)}
            onMouseEnter={() => setSelectedIndex(idx)}
            className={clsx(
              "w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors",
              idx === selectedIndex
                ? "bg-[var(--accent-light)]"
                : "hover:bg-[var(--bg-tertiary)]"
            )}
          >
            <div
              className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              <Wand2 size={14} className="text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-[var(--text-primary)] truncate">
                {pick.label}
              </div>
              {pick.subtitle && (
                <div className="text-xs text-[var(--text-muted)] truncate">
                  {pick.subtitle}
                </div>
              )}
            </div>
          </button>
        ))}
      </div>
    );
  }

  return null;
};
