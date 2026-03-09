import React, { useState, useEffect, useRef, useCallback } from 'react';
import type { Agent } from '../types';
import { getAgentIcon } from '../utils/agentIcons';
import { clsx } from 'clsx';

interface MentionAutocompleteProps {
  input: string;
  agents: Agent[];
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  onSelect: (agentName: string, replaceFrom: number) => void;
}

/**
 * Floating autocomplete dropdown for @agent mentions in the chat input.
 *
 * Triggers when user types @ after whitespace or at start-of-line.
 * Filters the agent list by what follows the @.
 * Arrow keys + Enter/Tab to navigate/select, Escape to dismiss.
 */
export const MentionAutocomplete: React.FC<MentionAutocompleteProps> = ({
  input,
  agents,
  textareaRef,
  onSelect,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [mentionStart, setMentionStart] = useState(-1);
  const [filter, setFilter] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Find the active @mention in the input
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const cursorPos = textarea.selectionStart;
    const textBefore = input.slice(0, cursorPos);

    // Look backwards from cursor for an @ that's at start-of-line or after whitespace
    const match = textBefore.match(/(?:^|\s)@([a-z_]*)$/);
    if (match) {
      // Find the actual @ position (match[0] may include the leading whitespace)
      const atPos = textBefore.length - match[0].length + (match[0].startsWith('@') ? 0 : 1);
      setMentionStart(atPos);
      setFilter(match[1]);
      setIsOpen(true);
      setSelectedIndex(0);
    } else {
      setIsOpen(false);
      setMentionStart(-1);
      setFilter('');
    }
  }, [input, textareaRef]);

  // Filter agents by the typed text after @
  const filteredAgents = agents.filter(a =>
    a.name.toLowerCase().includes(filter.toLowerCase()) ||
    a.display_name.toLowerCase().includes(filter.toLowerCase())
  );

  // Close if no matches
  useEffect(() => {
    if (isOpen && filteredAgents.length === 0) {
      setIsOpen(false);
    }
  }, [isOpen, filteredAgents.length]);

  // Handle keyboard navigation
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (!isOpen || filteredAgents.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => (prev + 1) % filteredAgents.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => (prev - 1 + filteredAgents.length) % filteredAgents.length);
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      e.stopPropagation();
      const agent = filteredAgents[selectedIndex];
      if (agent) {
        onSelect(agent.name, mentionStart);
        setIsOpen(false);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setIsOpen(false);
    }
  }, [isOpen, filteredAgents, selectedIndex, mentionStart, onSelect]);

  // Attach keyboard handler to textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.addEventListener('keydown', handleKeyDown, true);
    return () => textarea.removeEventListener('keydown', handleKeyDown, true);
  }, [textareaRef, handleKeyDown]);

  if (!isOpen || filteredAgents.length === 0) return null;

  return (
    <div
      ref={dropdownRef}
      className="absolute bottom-full left-0 right-0 mb-2 bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-lg z-50 overflow-hidden max-h-64 overflow-y-auto"
    >
      <div className="px-3 py-1.5 text-[10px] font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border-color)]">
        Mention an agent
      </div>
      {filteredAgents.map((agent, idx) => {
        const AgentIcon = getAgentIcon(agent.icon);
        return (
          <button
            key={agent.name}
            onClick={() => {
              onSelect(agent.name, mentionStart);
              setIsOpen(false);
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
              <AgentIcon size={14} className="text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-[var(--text-primary)] truncate">
                {agent.display_name}
              </div>
              <div className="text-xs text-[var(--text-muted)] truncate">
                @{agent.name}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
};
