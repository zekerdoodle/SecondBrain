import React, { useRef, useEffect, useState } from 'react';
import { Plus, X, Columns, ExternalLink } from 'lucide-react';
import { clsx } from 'clsx';
import type { ChatTab, Agent } from '../types';
import { getAgentIcon } from '../utils/agentIcons';
import { ContextMenu, type MenuItemOrSeparator } from './ContextMenu';

interface ChatTabBarProps {
  tabs: ChatTab[];
  activeSessionId: string;
  onTabClick: (sessionId: string) => void;
  onTabClose: (sessionId: string) => void;
  onNewChat: () => void;
  getAgent: (name: string) => Agent | undefined;
  onContextAction?: (action: string, sessionId: string) => void;
  isSecondary?: boolean;
  onCloseSplit?: () => void;
}

export const ChatTabBar: React.FC<ChatTabBarProps> = ({
  tabs,
  activeSessionId,
  onTabClick,
  onTabClose,
  onNewChat,
  getAgent,
  onContextAction,
  isSecondary = false,
  onCloseSplit,
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeTabRef = useRef<HTMLButtonElement>(null);

  // Context menu state
  const [contextMenu, setContextMenu] = useState<{
    isOpen: boolean;
    x: number;
    y: number;
    tabSessionId: string;
  }>({ isOpen: false, x: 0, y: 0, tabSessionId: '' });

  // Scroll active tab into view when it changes
  useEffect(() => {
    if (activeTabRef.current) {
      activeTabRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }, [activeSessionId]);

  if (tabs.length === 0 && !isSecondary) return null;

  const contextMenuItems: MenuItemOrSeparator[] = [
    {
      label: 'Open in Split View',
      icon: <Columns size={14} />,
      onClick: () => {
        onContextAction?.('split', contextMenu.tabSessionId);
      },
    },
    {
      label: 'Open in New Window',
      icon: <ExternalLink size={14} />,
      onClick: () => {
        onContextAction?.('popout', contextMenu.tabSessionId);
      },
    },
    { type: 'separator' as const },
    {
      label: 'Close Tab',
      icon: <X size={14} />,
      onClick: () => {
        onTabClose(contextMenu.tabSessionId);
      },
    },
    {
      label: 'Close Other Tabs',
      onClick: () => {
        onContextAction?.('closeOthers', contextMenu.tabSessionId);
      },
    },
  ];

  return (
    <div className="flex items-center border-b border-[var(--border-color)] bg-[var(--bg-secondary)] relative">
      {/* Secondary panel indicator + close button */}
      {isSecondary && onCloseSplit && (
        <button
          onClick={onCloseSplit}
          className="flex-shrink-0 p-1.5 ml-1 hover:bg-[var(--bg-tertiary)] rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          title="Close split view"
        >
          <X size={14} />
        </button>
      )}

      {/* Scrollable tab area */}
      <div
        ref={scrollRef}
        className="flex-1 flex items-center overflow-x-auto scrollbar-hide"
      >
        {tabs.map((tab) => {
          const isActive = tab.sessionId === activeSessionId;
          const agentObj = tab.agent ? getAgent(tab.agent) : undefined;
          const AgentIcon = getAgentIcon(agentObj?.icon);

          return (
            <button
              key={tab.sessionId}
              ref={isActive ? activeTabRef : undefined}
              onClick={() => onTabClick(tab.sessionId)}
              onContextMenu={(e) => {
                e.preventDefault();
                setContextMenu({
                  isOpen: true,
                  x: e.clientX,
                  y: e.clientY,
                  tabSessionId: tab.sessionId,
                });
              }}
              className={clsx(
                "group relative flex items-center gap-1.5 px-3 py-2 text-xs font-medium whitespace-nowrap transition-all border-b-2 min-w-0 max-w-[180px] shrink-0",
                isActive
                  ? "border-[var(--accent-primary)] text-[var(--text-primary)] bg-[var(--bg-primary)]"
                  : "border-transparent text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]"
              )}
            >
              {/* Agent icon */}
              <AgentIcon size={12} className="flex-shrink-0 text-[var(--accent-primary)]" />

              {/* Tab title */}
              <span className="truncate">
                {tab.title || 'New Chat'}
              </span>

              {/* Unread dot */}
              {tab.hasUnread && !isActive && (
                <span className="w-2 h-2 rounded-full bg-[var(--accent-primary)] animate-pulse-subtle flex-shrink-0" />
              )}

              {/* Close button */}
              <span
                onClick={(e) => {
                  e.stopPropagation();
                  onTabClose(tab.sessionId);
                }}
                className={clsx(
                  "p-0.5 rounded hover:bg-[var(--border-color)] transition-opacity flex-shrink-0",
                  isActive ? "opacity-60 hover:opacity-100" : "opacity-0 group-hover:opacity-60 hover:!opacity-100"
                )}
              >
                <X size={12} />
              </span>
            </button>
          );
        })}
      </div>

      {/* New chat button - pinned right */}
      <button
        onClick={onNewChat}
        className="flex-shrink-0 p-2 mx-1 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
        title="New chat"
      >
        <Plus size={14} />
      </button>

      {/* Context menu */}
      <ContextMenu
        isOpen={contextMenu.isOpen}
        x={contextMenu.x}
        y={contextMenu.y}
        items={contextMenuItems}
        onClose={() => setContextMenu(prev => ({ ...prev, isOpen: false }))}
      />
    </div>
  );
};
