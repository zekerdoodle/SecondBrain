import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, UserRound } from 'lucide-react';
import { clsx } from 'clsx';
import type { Agent } from '../types';
import { getAgentIcon } from '../utils/agentIcons';

interface AgentSelectorProps {
  agents: Agent[];
  selectedAgent: Agent | undefined;
  currentChatAgent: string | null; // null = new chat (unlocked)
  onSelect: (agent: Agent) => void;
}

export const AgentSelector: React.FC<AgentSelectorProps> = ({
  agents,
  selectedAgent,
  currentChatAgent,
  onSelect,
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const locked = currentChatAgent !== null;

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const agent = selectedAgent;
  const hasAgent = !!agent;
  const IconComponent = hasAgent ? getAgentIcon(agent?.icon) : UserRound;
  const displayName = agent?.display_name || 'Select agent';

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => !locked && setOpen(!open)}
        className={clsx(
          "flex items-center gap-2 transition-colors",
          locked ? "cursor-default" : "hover:opacity-80 cursor-pointer"
        )}
      >
        <div
          className={clsx(
            "w-8 h-8 rounded-lg flex items-center justify-center",
            !hasAgent && "border border-dashed border-[var(--border-color)]"
          )}
          style={hasAgent ? { backgroundColor: 'var(--accent-primary)' } : undefined}
        >
          <IconComponent
            size={18}
            className={hasAgent ? 'text-white' : 'text-[var(--text-muted)]'}
          />
        </div>
        <span
          className={clsx(
            "font-semibold tracking-tight",
            hasAgent ? "text-[var(--text-primary)]" : "text-[var(--text-muted)]"
          )}
        >
          {displayName}
        </span>
        {!locked && agents.length > 1 && (
          <ChevronDown size={14} className="text-[var(--text-muted)]" />
        )}
      </button>

      {open && !locked && (
        <div className="absolute top-full left-0 mt-2 w-64 max-h-[min(70vh,500px)] overflow-y-auto bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-lg z-50">
          {agents.map(a => {
            const AIcon = getAgentIcon(a.icon);
            const isSelected = a.name === agent?.name;
            return (
              <button
                key={a.name}
                onClick={() => { onSelect(a); setOpen(false); }}
                className={clsx(
                  "w-full flex items-center gap-3 px-4 py-3 text-left transition-colors",
                  isSelected
                    ? "bg-[var(--accent-light)]"
                    : "hover:bg-[var(--bg-tertiary)]"
                )}
              >
                <div
                  className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{ backgroundColor: 'var(--accent-primary)' }}
                >
                  <AIcon size={14} className="text-white" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-[var(--text-primary)] truncate">
                    {a.display_name}
                  </div>
                  <div className="text-xs text-[var(--text-muted)] truncate">
                    {a.model}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
