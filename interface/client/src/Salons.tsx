// Salons UI — sidebar list of salons + active salon view.
//
// Self-contained panel rendered alongside (or in place of) the chat panel.
// State comes from useSalons() which owns its own WebSocket subscriber.
//
// Visual notes:
// - Convener decisions render as dim "→ <agent>" pills above each agent
//   message (anchored via convener_decision.from_message_id).
// - Typing indicator: "<agent> is typing..." while the convener has called
//   them and we're awaiting their reply.
// - Active state and recheck interval shown in the salon header.

import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Send, Users, X, Trash2, MessageSquare, User, Menu, ChevronDown, ChevronRight } from 'lucide-react';
import { clsx } from 'clsx';
import MDEditor from '@uiw/react-md-editor';
import { useSalons } from './useSalons';
import { escapeNonHtmlTags } from './utils/escapeNonHtmlTags';
import { getAgentIcon } from './utils/agentIcons';
import { BlockRenderer } from './components/BlockView';
import type { Agent, ConvenerDecision, SalonFull, SalonMessage, ContentBlock } from './types';

interface SalonsProps {
  onClose?: () => void;
  agents: Agent[];          // for the create dialog + add-participant UI
  isMobile?: boolean;       // when true, Enter inserts newline (must tap Send)
}

const USER = 'user';

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const same = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (same) return time;
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
}

function formatRelative(ts: number | null | undefined): string {
  if (!ts) return '';
  const delta = Date.now() / 1000 - ts;
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export const Salons: React.FC<SalonsProps> = ({ onClose, agents, isMobile = false }) => {
  const {
    salons,
    activeSalonId,
    activeSalon,
    pendingConvener,
    typingByAgent,
    streamingByAgent,
    setActiveSalonId,
    refreshActive,
    create,
    post,
    addParticipant,
    remove,
    rename,
  } = useSalons();

  const [showCreate, setShowCreate] = useState(false);
  const [showAddParticipant, setShowAddParticipant] = useState(false);
  const [draftMsg, setDraftMsg] = useState('');
  const [posting, setPosting] = useState(false);
  // (messagesEndRef removed — auto-scroll now uses scrollRef + ResizeObserver,
  // mirroring Chat.tsx instead of scrollIntoView.)
  // Sidebar: default open on desktop, closed on mobile.
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 768 : true
  );

  const currentTyping = activeSalonId ? typingByAgent[activeSalonId] : null;
  const currentStreaming = activeSalonId ? streamingByAgent[activeSalonId] : null;
  const currentTypingAgent = currentTyping ? agents.find(a => a.name === currentTyping) : null;

  // Scroll plumbing — mirrors Chat.tsx exactly.
  // scrollRef = the scrollable messages container.
  // isUserNearBottom = ref tracking whether user is within 150px of the bottom.
  // The ResizeObserver keeps the user pinned to the bottom as content grows
  // (streaming blocks, tool calls, partial replies) IF they were already near
  // the bottom. If they've scrolled up, it leaves them alone.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const isUserNearBottom = useRef(true);
  const prevScrollAnchor = useRef<{ lastId: string; contentLen: number; streamingLen: number }>({
    lastId: '',
    contentLen: 0,
    streamingLen: 0,
  });
  const scrollOnLoad = useRef(true);

  // Track whether user has scrolled away from the bottom.
  // CRITICAL: deps include `activeSalonId` because the scroll container
  // (`<div ref={scrollRef}>`) is conditionally rendered — it doesn't exist
  // when no salon is selected. Empty deps `[]` (the Chat.tsx pattern) only
  // works there because Chat remounts per conversation; Salons stays mounted
  // across the no-salon state. With `[]`, scrollRef.current is null on first
  // mount, the listener never attaches, and isUserNearBottom stays true
  // forever → autoscroll fires regardless of scroll position.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const handleScroll = () => {
      const threshold = 150;
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      isUserNearBottom.current = distanceFromBottom <= threshold;
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, [activeSalonId]);

  // ResizeObserver: keep user pinned to bottom as content grows during streaming.
  // Critical for tool blocks, thinking blocks, and partial-text deltas — these
  // grow the DOM without firing scroll events, so without this, isUserNearBottom
  // would silently flip false and auto-scroll would stop.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const inner = el.firstElementChild as HTMLElement | null;
    if (!inner) return;
    const observer = new ResizeObserver(() => {
      if (isUserNearBottom.current) {
        el.scrollTop = el.scrollHeight;
      } else {
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        isUserNearBottom.current = distFromBottom <= 150;
      }
    });
    observer.observe(inner);
    return () => observer.disconnect();
  }, [activeSalonId]);

  // Reset scroll-on-load when active salon changes
  useEffect(() => {
    scrollOnLoad.current = true;
    isUserNearBottom.current = true;
    prevScrollAnchor.current = { lastId: '', contentLen: 0, streamingLen: 0 };
  }, [activeSalonId]);

  // Auto-scroll on new content (finalized messages OR streaming blocks growing).
  // Keyed on messages length AND streaming block content — captures both
  // "new finalized message arrived" and "streaming partial got longer".
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !activeSalon) return;

    // One-time scroll to bottom when opening / switching salons
    if (scrollOnLoad.current && activeSalon.messages.length > 0) {
      scrollOnLoad.current = false;
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
        isUserNearBottom.current = true;
      });
      return;
    }

    const lastMsg = activeSalon.messages[activeSalon.messages.length - 1];
    const lastId = lastMsg?.id || '';
    const contentLen = lastMsg
      ? (lastMsg.blocks
          ? lastMsg.blocks.reduce((sum, b) => sum + (b.content?.length || 0), 0)
          : (lastMsg.content?.length || 0))
      : 0;
    const streamingLen = currentStreaming?.blocks
      ? currentStreaming.blocks.reduce((sum, b) => sum + (b.content?.length || 0), 0)
      : 0;

    const prev = prevScrollAnchor.current;
    const hasNewContent =
      lastId !== prev.lastId ||
      contentLen > prev.contentLen ||
      streamingLen !== prev.streamingLen;
    prevScrollAnchor.current = { lastId, contentLen, streamingLen };

    if (hasNewContent && isUserNearBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [activeSalon?.messages, currentStreaming, pendingConvener]);

  // Periodic re-fetch of the active salon (catches anything missed via WS)
  useEffect(() => {
    if (!activeSalonId) return;
    const t = setInterval(() => { refreshActive(); }, 5000);
    return () => clearInterval(t);
  }, [activeSalonId, refreshActive]);

  const handlePost = async () => {
    const text = draftMsg.trim();
    if (!text || !activeSalonId || posting) return;
    setPosting(true);
    try {
      await post(text);
      setDraftMsg('');
    } catch (e: any) {
      alert(`Failed to post: ${e?.message || e}`);
    } finally {
      setPosting(false);
    }
  };

  return (
    <div className="h-full flex bg-[var(--bg-primary)] relative">
      {/* Sidebar — collapsible. On mobile (<md) it overlays the salon view
          via absolute positioning + z-index + backdrop. On desktop (md+) it's
          a normal flex child. Toggle via the Menu button in the salon header. */}
      {sidebarOpen && (
        <div
          className="md:hidden absolute inset-0 bg-black/40 z-10"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      <div
        className={clsx(
          'w-60 border-r border-[var(--border-color)] flex-col bg-[var(--bg-secondary)]',
          // Positioning: overlay on mobile, normal flex child on desktop.
          'absolute inset-y-0 left-0 z-20 md:relative',
          // Visibility: open => flex (at all breakpoints), closed => hidden.
          // Do NOT add `md:flex` unconditionally — it overrides `hidden` and
          // breaks the desktop collapse.
          sidebarOpen ? 'flex' : 'hidden'
        )}
      >
        <div className="h-12 px-3 border-b border-[var(--border-color)] flex items-center justify-between">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setSidebarOpen(false)}
              className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
              title="Hide sidebar"
            >
              <Menu size={16} />
            </button>
            <span className="text-sm font-semibold text-[var(--text-primary)]">Salons</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setShowCreate(true)}
              className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
              title="New salon"
            >
              <Plus size={16} />
            </button>
            {onClose && (
              <button
                onClick={onClose}
                className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
                title="Close salons"
              >
                <X size={16} />
              </button>
            )}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {salons.length === 0 && (
            <div className="p-4 text-xs text-[var(--text-muted)] text-center">
              No salons yet. Click <Plus size={12} className="inline" /> to start one.
            </div>
          )}
          {salons.map(s => {
            const isActive = s.salon_id === activeSalonId;
            return (
              <button
                key={s.salon_id}
                onClick={() => setActiveSalonId(s.salon_id)}
                className={clsx(
                  'w-full text-left px-3 py-2 border-l-2 transition-colors',
                  isActive
                    ? 'border-[var(--accent-primary)] bg-[var(--bg-tertiary)]'
                    : 'border-transparent hover:bg-[var(--bg-tertiary)]'
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-[var(--text-primary)] truncate">
                    {s.title || '(untitled)'}
                  </span>
                  {!s.gc_active && (
                    <span className="text-[9px] uppercase tracking-wide text-[var(--text-muted)] shrink-0">parked</span>
                  )}
                </div>
                <div className="flex items-center gap-1 mt-0.5">
                  <Users size={10} className="text-[var(--text-muted)] shrink-0" />
                  <span className="text-[10px] text-[var(--text-muted)] truncate">
                    {(s.participants || []).join(', ')}
                  </span>
                </div>
                <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
                  {s.message_count} msgs · {formatRelative(s.last_message_at)}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Active salon view */}
      <div className="flex-1 flex flex-col min-w-0">
        {!activeSalon && (
          <>
            {/* When no salon is active, the SalonHeader isn't rendered, so
                expose a hamburger here so mobile users can still open the
                sidebar to pick / create one. */}
            {!sidebarOpen && (
              <div className="md:hidden h-12 px-3 border-b border-[var(--border-color)] flex items-center bg-[var(--bg-secondary)]">
                <button
                  onClick={() => setSidebarOpen(true)}
                  className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
                  title="Show salons"
                >
                  <Menu size={16} />
                </button>
              </div>
            )}
            <div className="flex-1 flex items-center justify-center text-sm text-[var(--text-muted)]">
              <div className="text-center">
                <MessageSquare size={32} className="mx-auto mb-2 opacity-40" />
                <div>Pick a salon — or start a new one.</div>
              </div>
            </div>
          </>
        )}

        {activeSalon && (
          <>
            <SalonHeader
              salon={activeSalon}
              sidebarOpen={sidebarOpen}
              onToggleSidebar={() => setSidebarOpen(v => !v)}
              onAddParticipant={() => setShowAddParticipant(true)}
              onRename={(title) => rename(activeSalon.salon_id, title).catch(console.error)}
              onDelete={() => {
                if (!confirm(`Delete salon "${activeSalon.title}"?`)) return;
                remove(activeSalon.salon_id).catch(console.error);
              }}
            />

            <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3">
              <div className="space-y-3">
              {activeSalon.messages.map(msg => (
                <SalonMessageView key={msg.id} msg={msg} agents={agents} />
              ))}
              {/* In-flight convener decision (anchored to last message) */}
              {pendingConvener && pendingConvener.salonId === activeSalon.salon_id &&
                pendingConvener.anchorMsgId === (activeSalon.messages[activeSalon.messages.length - 1]?.id || null) && (
                  <ConvenerPill decision={pendingConvener.decision} />
                )}
              {/* Live streaming partial — agent's blocks as they arrive */}
              {currentStreaming && currentStreaming.blocks && currentStreaming.blocks.length > 0 && (
                <SalonStreamingView
                  agentName={currentStreaming.from}
                  blocks={currentStreaming.blocks}
                  agents={agents}
                />
              )}
              {currentTyping && !currentStreaming && (
                <div className="flex items-center gap-2 text-xs text-[var(--text-muted)] italic">
                  <span className="w-2 h-2 bg-[var(--accent-primary)] rounded-full animate-pulse" />
                  {(currentTypingAgent?.display_name || currentTyping)} is thinking...
                </div>
              )}
              </div>
            </div>

            {/* Input bar */}
            <div className="border-t border-[var(--border-color)] p-3 bg-[var(--bg-secondary)]">
              <div className="flex gap-2">
                <textarea
                  value={draftMsg}
                  onChange={(e) => setDraftMsg(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      if (isMobile) {
                        // Mobile: Enter adds a newline. Tap Send to post.
                        return;
                      } else if (!e.shiftKey) {
                        // Desktop: Enter sends, Shift+Enter newline.
                        e.preventDefault();
                        handlePost();
                      }
                    }
                  }}
                  placeholder={isMobile ? "Message the salon... (tap Send to post)" : "Message the salon... (Enter to send)"}
                  rows={2}
                  className="flex-1 resize-none rounded px-3 py-2 text-sm bg-[var(--bg-primary)] border border-[var(--border-color)] focus:outline-none focus:border-[var(--accent-primary)] text-[var(--text-primary)]"
                />
                <button
                  onClick={handlePost}
                  disabled={!draftMsg.trim() || posting}
                  className="self-stretch px-3 rounded bg-[var(--accent-primary)] text-white hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center"
                  title="Send"
                >
                  <Send size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {showCreate && (
        <CreateSalonModal
          agents={agents}
          onCancel={() => setShowCreate(false)}
          onCreate={async (opts) => {
            try {
              const id = await create(opts);
              setShowCreate(false);
              setActiveSalonId(id);
            } catch (e: any) {
              alert(`Create failed: ${e?.message || e}`);
            }
          }}
        />
      )}

      {showAddParticipant && activeSalon && (
        <AddParticipantModal
          agents={agents}
          existing={activeSalon.participants}
          onCancel={() => setShowAddParticipant(false)}
          onAdd={async (name) => {
            try {
              await addParticipant(name);
              setShowAddParticipant(false);
            } catch (e: any) {
              alert(`Add failed: ${e?.message || e}`);
            }
          }}
        />
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------

const SalonHeader: React.FC<{
  salon: SalonFull;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onAddParticipant: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}> = ({ salon, sidebarOpen, onToggleSidebar, onAddParticipant, onRename, onDelete }) => {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(salon.title);

  useEffect(() => { setTitle(salon.title); }, [salon.title]);

  const recheckLabel = salon.gc_recheck_minutes
    ? `recheck every ${salon.gc_recheck_minutes}m`
    : '';
  const stateLabel = salon.gc_active ? 'active' : 'parked';

  return (
    <div className="border-b border-[var(--border-color)] px-4 py-2 bg-[var(--bg-secondary)] flex items-center justify-between gap-2">
      {/* Sidebar toggle: always visible on mobile, only when sidebar is hidden on desktop */}
      <button
        onClick={onToggleSidebar}
        className={clsx(
          'shrink-0 p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]',
          sidebarOpen ? 'md:hidden' : ''
        )}
        title={sidebarOpen ? 'Hide salons sidebar' : 'Show salons sidebar'}
      >
        <Menu size={16} />
      </button>
      <div className="min-w-0 flex-1">
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (title.trim() && title !== salon.title) onRename(title.trim());
              setEditing(false);
            }}
          >
            <input
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => {
                if (title.trim() && title !== salon.title) onRename(title.trim());
                setEditing(false);
              }}
              className="text-sm font-semibold bg-transparent border-b border-[var(--accent-primary)] outline-none text-[var(--text-primary)] w-full"
            />
          </form>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="text-sm font-semibold text-[var(--text-primary)] truncate text-left hover:opacity-80"
            title="Click to rename"
          >
            {salon.title}
          </button>
        )}
        <div className="text-[10px] text-[var(--text-muted)] flex items-center gap-2 mt-0.5">
          <span>{(salon.participants || []).join(', ')}</span>
          <span>·</span>
          <span>{stateLabel}</span>
          {recheckLabel && <span>· {recheckLabel}</span>}
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0 ml-2">
        <button
          onClick={onAddParticipant}
          className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
          title="Add participant"
        >
          <Users size={14} />
        </button>
        <button
          onClick={onDelete}
          className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-red-500"
          title="Delete salon"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------

// Render the avatar circle for a participant — the user gets a User glyph, agents
// get their configured SVG icon (from `.claude/agents/<name>/config.yaml`'s
// `icon` field, mapped via `getAgentIcon`).
const ParticipantAvatar: React.FC<{
  from: string;
  agents: Agent[];
}> = ({ from, agents }) => {
  const isZeke = from === USER;
  const agent = agents.find(a => a.name === from);

  return (
    <div className={clsx(
      'shrink-0 w-7 h-7 rounded-full flex items-center justify-center',
      isZeke
        ? 'bg-[var(--accent-primary)] text-white'
        : 'bg-[var(--bg-tertiary)] text-[var(--text-primary)]'
    )}>
      {isZeke ? (
        <User size={14} />
      ) : agent ? (
        // Render agent's configured SVG icon
        (() => {
          const Icon = getAgentIcon(agent.icon);
          return <Icon size={14} />;
        })()
      ) : (
        // Fallback for unknown participants — first two letters
        <span className="text-[10px] font-semibold">{from.slice(0, 2)}</span>
      )}
    </div>
  );
};

const SalonMessageView: React.FC<{
  msg: SalonMessage;
  agents: Agent[];
}> = ({ msg, agents }) => {
  const isZeke = msg.from === USER;
  // Only show the convener pill above the FIRST agent in a chain. Without
  // this filter, a multi-agent decision like "→ character → patch" renders the
  // same pill above every agent message in the chain (one duplicate per
  // additional agent). chain_index is set by the dispatcher when it appends
  // each agent's reply; older messages without it default to showing the
  // pill (legacy compat).
  const isChainHead =
    msg.convener_decision &&
    (msg.convener_decision.chain_index === undefined ||
      msg.convener_decision.chain_index === 0);

  // Prefer rich block rendering when the message has blocks (streaming-aware
  // agent replies). Falls back to plain markdown for legacy messages and the user's
  // posts.
  const hasBlocks = !isZeke && msg.blocks && msg.blocks.length > 0;
  const agent = agents.find(a => a.name === msg.from);
  const displayName = agent?.display_name || msg.from;

  return (
    <div className="space-y-1">
      {/* Convener pill above the message (shows the decision that led to it) */}
      {msg.convener_decision && !isZeke && isChainHead && (
        <ConvenerPill decision={msg.convener_decision} />
      )}
      <div className="flex gap-2">
        <ParticipantAvatar from={msg.from} agents={agents} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-xs font-semibold text-[var(--text-primary)]">{displayName}</span>
            <span className="text-[10px] text-[var(--text-muted)]">{formatTime(msg.created_at)}</span>
          </div>
          {hasBlocks ? (
            <div className="mt-1">
              <BlockRenderer blocks={msg.blocks!} />
            </div>
          ) : (
            <div
              className="prose max-w-none chat-markdown font-chat mt-0.5"
              style={{
                fontFamily: 'var(--font-chat)',
                fontSize: 'var(--font-size-base)',
              }}
            >
              <MDEditor.Markdown
                source={escapeNonHtmlTags(msg.content || '')}
                style={{
                  backgroundColor: 'transparent',
                  color: 'inherit',
                  fontFamily: 'var(--font-chat)',
                  fontSize: 'var(--font-size-base)',
                  lineHeight: '1.6',
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// SalonStreamingView — renders the in-flight reply for an agent currently
// producing tokens. Same shape as SalonMessageView but driven by ephemeral
// streaming state from useSalons.
// ---------------------------------------------------------------------------

const SalonStreamingView: React.FC<{
  agentName: string;
  blocks: ContentBlock[];
  agents: Agent[];
}> = ({ agentName, blocks, agents }) => {
  const agent = agents.find(a => a.name === agentName);
  const displayName = agent?.display_name || agentName;
  return (
    <div className="space-y-1">
      <div className="flex gap-2">
        <ParticipantAvatar from={agentName} agents={agents} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-xs font-semibold text-[var(--text-primary)]">{displayName}</span>
            <span className="text-[10px] text-[var(--text-muted)] italic">streaming…</span>
          </div>
          <div className="mt-1">
            <BlockRenderer blocks={blocks} />
          </div>
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------

// Convener decision pill — collapsed by default, click to expand and see
// the full structured decision (targets, active state, reasoning). Modeled
// on `ToolChipBlock` in BlockView.tsx so it feels familiar.
const ConvenerPill: React.FC<{ decision: ConvenerDecision }> = ({ decision }) => {
  const [expanded, setExpanded] = useState(false);
  const targets = decision.invoke_agent_in_gc;
  const label = targets.length === 0
    ? '→ wait for human'
    : `→ ${targets.join(' → ')}`;

  // Translate gc_active_or_not (string|number per the type) into a label.
  const activeRaw = decision.gc_active_or_not;
  let activeLabel: string;
  if (activeRaw === 'yes') activeLabel = 'active';
  else if (activeRaw === '' || activeRaw === null || activeRaw === undefined) activeLabel = 'unchanged';
  else if (activeRaw === 'no') activeLabel = 'park';
  else if (typeof activeRaw === 'number') activeLabel = `recheck in ${activeRaw}m`;
  else activeLabel = String(activeRaw);

  return (
    <div className="flex flex-col">
      <button
        onClick={() => setExpanded(v => !v)}
        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] text-[var(--text-muted)] bg-[var(--bg-tertiary)] border border-[var(--border-color)] hover:bg-[var(--bg-primary)] hover:text-[var(--text-secondary)] transition-colors max-w-full self-start"
        title="Click for convener reasoning"
      >
        <span className="opacity-70">convener</span>
        <span className="truncate">{label}</span>
        {expanded ? (
          <ChevronDown size={11} className="flex-shrink-0 opacity-60" />
        ) : (
          <ChevronRight size={11} className="flex-shrink-0 opacity-60" />
        )}
      </button>
      {expanded && (
        <div className="mt-1.5 ml-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-secondary)] p-2.5 text-[11px] space-y-1.5 max-w-2xl">
          <div className="flex gap-2">
            <span className="font-mono text-[var(--text-muted)] flex-shrink-0 w-20">invoke:</span>
            <span className="font-mono text-[var(--text-primary)] break-all">
              {targets.length === 0 ? '(none — wait for human)' : targets.join(' → ')}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="font-mono text-[var(--text-muted)] flex-shrink-0 w-20">gc_active:</span>
            <span className="font-mono text-[var(--text-primary)]">{activeLabel}</span>
          </div>
          {decision.chain_index !== undefined && (
            <div className="flex gap-2">
              <span className="font-mono text-[var(--text-muted)] flex-shrink-0 w-20">chain_idx:</span>
              <span className="font-mono text-[var(--text-primary)]">{decision.chain_index}</span>
            </div>
          )}
          {decision.reasoning && (
            <div>
              <div className="font-mono text-[var(--text-muted)] mb-1">reasoning:</div>
              <div className="whitespace-pre-wrap text-[var(--text-secondary)] leading-relaxed">
                {decision.reasoning}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------

const CreateSalonModal: React.FC<{
  agents: Agent[];
  onCancel: () => void;
  onCreate: (opts: { title: string; participants: string[]; opening_message?: string }) => Promise<void>;
}> = ({ agents, onCancel, onCreate }) => {
  const [title, setTitle] = useState('');
  const [opening, setOpening] = useState('');
  const [picked, setPicked] = useState<string[]>([USER]);
  const [submitting, setSubmitting] = useState(false);

  const togglePicked = (name: string) => {
    setPicked(prev => prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]);
  };

  const submit = async () => {
    if (picked.length === 0) return;
    setSubmitting(true);
    try {
      await onCreate({
        // Empty title → backend stores "(untitled salon)" and salon_titler
        // auto-names it after the first exchange.
        title: title.trim(),
        participants: picked,
        opening_message: opening.trim() || undefined,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-[var(--bg-primary)] border border-[var(--border-color)] rounded-lg w-full max-w-md p-4 space-y-3 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">New salon</h3>
          <button onClick={onCancel} className="p-1 hover:bg-[var(--bg-tertiary)] rounded">
            <X size={14} />
          </button>
        </div>

        <div>
          <label className="block text-xs text-[var(--text-secondary)] mb-1">Title (optional)</label>
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Leave blank to auto-name after first exchange"
            className="w-full rounded px-2 py-1 text-sm bg-[var(--bg-secondary)] border border-[var(--border-color)] focus:outline-none focus:border-[var(--accent-primary)] text-[var(--text-primary)]"
          />
        </div>

        <div>
          <label className="block text-xs text-[var(--text-secondary)] mb-1">Participants</label>
          <div className="border border-[var(--border-color)] rounded p-2 max-h-48 overflow-y-auto bg-[var(--bg-secondary)]">
            <ParticipantToggle
              name={USER}
              displayName="the user (you)"
              checked={picked.includes(USER)}
              onToggle={() => togglePicked(USER)}
            />
            {agents.filter(a => a.chattable !== false).map(a => (
              <ParticipantToggle
                key={a.name}
                name={a.name}
                displayName={a.display_name || a.name}
                checked={picked.includes(a.name)}
                onToggle={() => togglePicked(a.name)}
              />
            ))}
          </div>
        </div>

        <div>
          <label className="block text-xs text-[var(--text-secondary)] mb-1">Opening message (optional)</label>
          <textarea
            value={opening}
            onChange={(e) => setOpening(e.target.value)}
            placeholder="Set the topic..."
            rows={2}
            className="w-full rounded px-2 py-1 text-sm bg-[var(--bg-secondary)] border border-[var(--border-color)] focus:outline-none focus:border-[var(--accent-primary)] text-[var(--text-primary)] resize-none"
          />
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onCancel} className="px-3 py-1 text-sm rounded text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]">Cancel</button>
          <button
            onClick={submit}
            disabled={picked.length === 0 || submitting}
            className="px-3 py-1 text-sm rounded bg-[var(--accent-primary)] text-white disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
};

const ParticipantToggle: React.FC<{
  name: string;
  displayName: string;
  checked: boolean;
  onToggle: () => void;
}> = ({ name, displayName, checked, onToggle }) => (
  <label className="flex items-center gap-2 px-1 py-1 cursor-pointer hover:bg-[var(--bg-tertiary)] rounded text-sm text-[var(--text-primary)]">
    <input type="checkbox" checked={checked} onChange={onToggle} className="cursor-pointer" />
    <span>{displayName}</span>
    <span className="text-[10px] text-[var(--text-muted)] ml-auto">{name}</span>
  </label>
);

// ---------------------------------------------------------------------------

const AddParticipantModal: React.FC<{
  agents: Agent[];
  existing: string[];
  onCancel: () => void;
  onAdd: (name: string) => Promise<void>;
}> = ({ agents, existing, onCancel, onAdd }) => {
  const candidates = useMemo(() => {
    const all = [
      { name: USER, displayName: 'the user' },
      ...agents.map(a => ({ name: a.name, displayName: a.display_name || a.name })),
    ];
    return all.filter(c => !existing.includes(c.name));
  }, [agents, existing]);

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-[var(--bg-primary)] border border-[var(--border-color)] rounded-lg w-full max-w-sm p-4 space-y-3 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Add participant</h3>
          <button onClick={onCancel} className="p-1 hover:bg-[var(--bg-tertiary)] rounded">
            <X size={14} />
          </button>
        </div>
        <div className="border border-[var(--border-color)] rounded max-h-72 overflow-y-auto bg-[var(--bg-secondary)]">
          {candidates.length === 0 && (
            <div className="px-3 py-4 text-xs text-[var(--text-muted)] text-center">
              All agents are already in this salon.
            </div>
          )}
          {candidates.map(c => (
            <button
              key={c.name}
              onClick={() => onAdd(c.name)}
              className="w-full text-left px-3 py-2 hover:bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] flex items-center justify-between"
            >
              <span>{c.displayName}</span>
              <span className="text-[10px] text-[var(--text-muted)]">{c.name}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
