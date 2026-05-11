import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';
import {
  Send, Loader2, Plus, History, ChevronLeft, Pencil, RotateCcw, X,
  File as FileIcon, Trash2, MessageCircle, Clock, Square, Search,
  Check, CheckCheck, AlertCircle, Crown, ImagePlus, Circle, Copy,
  SmilePlus, Sparkles, Users
} from 'lucide-react';
import { promoteChatToSalon } from './salonApi';
import { ChatSearch } from './ChatSearch';
import { useClaude, type ClaudeHook, type NotificationData, type ChessGameState } from './useClaude';
import { useToast } from './Toast';
import { useTabFlash } from './hooks/useTabFlash';
import { useCodeBlockWrap } from './hooks/useCodeBlockWrap';
import { useAgents } from './hooks/useAgents';
import type { ChatMessage, ChatImageRef, FormField, ToolCallMessage } from './types';
import { clsx } from 'clsx';
import MDEditor from '@uiw/react-md-editor';
import { escapeNonHtmlTags } from './utils/escapeNonHtmlTags';
import { API_URL } from './config';
import { InlineForm } from './components/InlineForm';
import { ChessGame, useChessGame } from './components/ChessGame';
import { AgentSelector } from './components/AgentSelector';
import { MoodSelector } from './components/MoodSelector';
import { ChatTabBar } from './components/ChatTabBar';
import { getAgentIcon } from './utils/agentIcons';
import { MentionAutocomplete } from './components/MentionAutocomplete';
import { SlashCommandAutocomplete, type SlashCommand } from './components/SlashCommandAutocomplete';

import { ToolCallChips, type ToolCallData } from './components/ToolCallChips';
import { BlockRenderer } from './components/BlockView';
import type { ChatTab } from './types';
import EmojiPickerReact, { Theme as EmojiTheme } from 'emoji-picker-react';
import type { EmojiClickData } from 'emoji-picker-react';

// Accent color is now managed via CSS variables (--accent-primary)
const CHAT_TABS_KEY = 'second_brain_chat_tabs';
const CHAT_DRAFTS_KEY = 'second_brain_chat_drafts';
const MAX_CHAT_TABS = 8;

// Fun phrases for different generation phases
const INITIAL_PHRASES = [
  'Gathering thoughts...',
  'Fetching memories...',
  'Pulling it together...',
  'Rummaging through memory...',
  'Connecting the dots...',
  'Loading context...',
  'Recalling...',
  'Piecing things together...',
  'Dusting off the archives...',
  'Warming up...',
];

const STALL_PHRASES = [
  'Still working...',
  'Crunching away...',
  'Almost there...',
  'Hammering it out...',
  'Bear with me...',
  'Deep in thought...',
  'Wrangling the words...',
  'Cooking something up...',
  'Just a sec...',
  'Percolating...',
];


// File path detection for clickable links — shared utility
import { looksLikeFilePath, toRelativePath, isImagePath } from './utils/filePaths';
import { InlineFilePathImage } from './components/InlineFilePathImage';

// --- Emoji Reaction Components ---
const EmojiPicker = ({ onSelect, onClose }: { onSelect: (emoji: string) => void; onClose: () => void }) => {
  const ref = useRef<HTMLDivElement>(null);

  const handleEmojiClick = useCallback((emojiData: EmojiClickData) => {
    onSelect(emojiData.emoji);
  }, [onSelect]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      onTouchStart={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div ref={ref} className="rounded-xl overflow-hidden shadow-2xl">
        <EmojiPickerReact
          onEmojiClick={handleEmojiClick}
          theme={EmojiTheme.DARK}
          height={350}
          width={300}
          searchPlaceholder="Search emoji..."
          skinTonesDisabled={false}
          lazyLoadEmojis={true}
        />
      </div>
    </div>
  );
};

// Extract copyable text from a message — handles both legacy (content) and block-based formats
const getMessageText = (msg: ChatMessage, isUser: boolean): string => {
  // For user messages: use content directly (filter out [CONTEXT:] lines)
  if (isUser) {
    return msg.content.split('\n').filter(line => !line.startsWith('[CONTEXT:')).join('\n').trim();
  }
  // For assistant messages: prefer blocks (content is '' during/after streaming)
  if (msg.blocks && msg.blocks.length > 0) {
    return msg.blocks
      .filter(b => b.type === 'text')
      .map(b => b.content)
      .join('\n\n')
      .trim();
  }
  // Fallback to content (legacy messages loaded from disk)
  return msg.content;
};

// Small copy button used in the action row beside reactions
const CopyButton = ({ msg, isUser }: { msg: ChatMessage; isUser: boolean }) => {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    const textToCopy = getMessageText(msg, isUser);
    try {
      await navigator.clipboard.writeText(textToCopy);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = textToCopy;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }, [msg.content, msg.blocks, isUser]);

  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-all opacity-0 group-hover:opacity-60 hover:!opacity-100 focus:opacity-100"
      title={copied ? "Copied!" : "Copy message"}
    >
      {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
    </button>
  );
};

const ReactionBar = ({ msg, onToggle, isActive = false, readOnly = false, alignRight = false }: { msg: ChatMessage; onToggle: (emoji: string) => void; isActive?: boolean; readOnly?: boolean; alignRight?: boolean }) => {
  const [showPicker, setShowPicker] = useState(false);
  const reactions = msg.reactions;
  const hasReactions = reactions && Object.keys(reactions).length > 0;

  return (
    <div className={clsx("flex items-center gap-1 flex-wrap", alignRight && "justify-end")}>
      {hasReactions && Object.entries(reactions!).map(([emoji, reactors]) => {
        const isOwn = reactors.includes('user');
        return (
          <button
            key={emoji}
            onClick={() => !readOnly && onToggle(emoji)}
            className={clsx(
              "inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs border transition-colors",
              readOnly ? "cursor-default" : "cursor-pointer",
              isOwn
                ? "border-[var(--accent-primary)]/50 bg-[var(--accent-primary)]/10 hover:bg-[var(--accent-primary)]/20"
                : "border-[var(--border-color)] bg-[var(--bg-tertiary)] hover:border-[var(--accent-primary)]/40"
            )}
          >
            <span>{emoji}</span>
            {reactors.length > 1 && <span className="text-[var(--text-muted)]">{reactors.length}</span>}
          </button>
        );
      })}
      {!readOnly && (
        <div className="relative">
          <button
            onClick={() => setShowPicker(!showPicker)}
            className={clsx(
              "p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-all",
              // Active (mobile tap) or has reactions: always show. Otherwise: hover-reveal only
              (isActive || hasReactions) ? "opacity-60 hover:opacity-100" : "opacity-0 group-hover:opacity-60 hover:!opacity-100"
            )}
            title="Add reaction"
          >
            <SmilePlus size={14} />
          </button>
          {showPicker && (
            <EmojiPicker
              onSelect={(emoji) => { onToggle(emoji); setShowPicker(false); }}
              onClose={() => setShowPicker(false)}
            />
          )}
        </div>
      )}
    </div>
  );
};

// --- Memoized Chat Message Component ---
// Prevents all messages from re-rendering when only the streaming message changes
interface ChatMessageProps {
  msg: ChatMessage;
  isUser: boolean;
  isContinuation: boolean;
  isEditing: boolean;
  editText: string;
  onEditTextChange: (text: string) => void;
  status: string;
  agentDisplayName: string;
  onStartEdit: (msg: ChatMessage) => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onFormSubmit: (formId: string, values: Record<string, any>) => void;
  onOpenFile?: (path: string) => void;
}

const ChatMessageItem = React.memo<ChatMessageProps>(({
  msg, isUser, isContinuation, isEditing,
  editText, onEditTextChange, status, agentDisplayName,
  onStartEdit, onCancelEdit, onSaveEdit, onFormSubmit, onOpenFile
}) => {
  return (
    <div className={clsx("flex flex-col w-full group", isContinuation && 'mt-2')}>
      <div className={clsx("flex flex-col", isUser ? "items-end" : "items-start w-full")}>
        {!isContinuation && (
          <div className={clsx(
            "flex items-center gap-2 mb-2 group/header",
            isUser ? "flex-row-reverse" : "flex-row"
          )}>
            <span className="text-xs font-medium text-[var(--text-muted)]">
              {isUser ? 'You' : agentDisplayName}
            </span>
            {status === 'idle' && isUser && (
              <button
                onClick={() => onStartEdit(msg)}
                className="p-1 hover:bg-[var(--bg-tertiary)] rounded text-[var(--text-muted)] hover:text-[var(--text-secondary)] opacity-0 group-hover/header:opacity-100 transition-opacity"
                title="Edit"
              >
                <Pencil size={12} />
              </button>
            )}
          </div>
        )}

        {msg.formData ? (
          <InlineForm formData={msg.formData} onSubmit={onFormSubmit} />
        ) : isEditing ? (
          <div className="w-full max-w-[90%] bg-[var(--bg-secondary)] rounded-2xl border border-[var(--border-color)] p-4 shadow-warm">
            <textarea
              value={editText}
              onChange={(e) => onEditTextChange(e.target.value)}
              className="w-full p-3 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20 outline-none resize-none text-sm"
              rows={4}
              autoFocus
            />
            <div className="flex justify-end gap-2 mt-3">
              <button
                onClick={onCancelEdit}
                className="px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={onSaveEdit}
                className="px-3 py-1.5 text-sm text-white rounded-lg transition-colors btn-primary"
                style={{ backgroundColor: 'var(--accent-primary)' }}
              >
                Save & Resend
              </button>
            </div>
          </div>
        ) : (
          <div className={clsx("flex flex-col", isUser ? "items-end max-w-[75%]" : "w-full")}>
            <div
              className={clsx(
                "rounded-2xl px-4 py-3 text-[15px] leading-relaxed",
                !msg.isStreaming && "animate-in",
                isUser
                  ? "bg-[var(--user-bg)] text-white rounded-br-md"
                  : "w-full bg-[var(--bg-secondary)] border border-[var(--border-color)] text-[var(--text-primary)] rounded-bl-md shadow-warm",
                msg.isError && "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300",
                msg.isStreaming && "border-[var(--accent-primary)]/30",
                msg.status === 'failed' && isUser && "border-2 border-red-400 opacity-80"
              )}
            >
            {isUser ? (
              (() => {
                const lines = msg.content.split('\n');
                const contextLines = lines.filter(line => line.startsWith('[CONTEXT:'));
                const displayText = lines.filter(line => !line.startsWith('[CONTEXT:')).join('\n').trim();
                const fileAttachments = contextLines.map(line => {
                  const path = line.replace('[CONTEXT: ', '').replace(']', '').trim();
                  const name = path.split('/').pop() || path;
                  return { name, path };
                });
                return (
                  <div>
                    {msg.images && msg.images.length > 0 && (
                      <div className={clsx("flex flex-wrap gap-2", (displayText || fileAttachments.length > 0) && "mb-2")}>
                        {msg.images.map(img => (
                          <img
                            key={img.id}
                            src={`${API_URL}/chat/images/${img.filename}`}
                            alt={img.originalName}
                            loading="lazy"
                            className="max-h-48 max-w-full rounded-lg cursor-pointer hover:opacity-90 transition-opacity"
                            onClick={() => window.open(`${API_URL}/chat/images/${img.filename}`, '_blank')}
                          />
                        ))}
                      </div>
                    )}
                    {displayText && (
                      <div className="prose max-w-none chat-markdown chat-markdown-user font-chat" style={{ fontFamily: 'var(--font-chat)', fontSize: 'var(--font-size-base)' }}>
                        <MDEditor.Markdown
                          source={escapeNonHtmlTags(displayText)}
                          style={{
                            backgroundColor: 'transparent',
                            color: 'inherit',
                            fontFamily: 'var(--font-chat)',
                            fontSize: 'var(--font-size-base)',
                            lineHeight: '1.7'
                          }}
                          components={{
                            code: ({ children, className, ...props }) => {
                              const isInline = !className;
                              const text = String(children).replace(/\n$/, '');
                              if (isInline && looksLikeFilePath(text)) {
                                const relativePath = toRelativePath(text);
                                if (isImagePath(relativePath)) {
                                  return (
                                    <InlineFilePathImage
                                      path={relativePath}
                                      originalText={text}
                                      onOpenFile={onOpenFile}
                                    />
                                  );
                                }
                                if (onOpenFile) {
                                  return (
                                    <code
                                      className="file-path-link"
                                      onClick={(e) => {
                                        e.preventDefault();
                                        e.stopPropagation();
                                        onOpenFile(relativePath);
                                      }}
                                      title={`Open ${relativePath} in editor`}
                                      {...props}
                                    >
                                      {children}
                                    </code>
                                  );
                                }
                              }
                              return <code className={className} {...props}>{children}</code>;
                            },
                            img: ({ src, alt, ...props }) => {
                              if (src && looksLikeFilePath(src)) {
                                const relativePath = toRelativePath(src);
                                if (isImagePath(relativePath)) {
                                  return (
                                    <InlineFilePathImage
                                      path={relativePath}
                                      originalText={src}
                                      alt={alt}
                                      onOpenFile={onOpenFile}
                                    />
                                  );
                                }
                              }
                              return <img src={src} alt={alt} {...props} />;
                            }
                          }}
                        />
                      </div>
                    )}
                    {fileAttachments.length > 0 && (
                      <div className={clsx("flex flex-wrap gap-1.5", displayText && "mt-2")}>
                        {fileAttachments.map(file => (
                          <div
                            key={file.path}
                            className="flex items-center gap-1.5 px-2 py-1 bg-white/15 rounded-full text-xs text-white/80"
                            title={file.path}
                          >
                            <FileIcon size={11} className="flex-shrink-0 opacity-70" />
                            <span className="max-w-[120px] truncate">{file.name}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })()
            ) : (
              <div className="prose max-w-none chat-markdown font-chat" style={{ fontFamily: 'var(--font-chat)', fontSize: 'var(--font-size-base)' }}>
                <MDEditor.Markdown
                  source={escapeNonHtmlTags(msg.content)}
                  style={{
                    backgroundColor: 'transparent',
                    color: 'inherit',
                    fontFamily: 'var(--font-chat)',
                    fontSize: 'var(--font-size-base)',
                    lineHeight: '1.7'
                  }}
                  components={{
                    code: ({ children, className, ...props }) => {
                      const isInline = !className;
                      const text = String(children).replace(/\n$/, '');
                      if (isInline && looksLikeFilePath(text)) {
                        const relativePath = toRelativePath(text);
                        if (isImagePath(relativePath)) {
                          return (
                            <InlineFilePathImage
                              path={relativePath}
                              originalText={text}
                              onOpenFile={onOpenFile}
                            />
                          );
                        }
                        if (onOpenFile) {
                          return (
                            <code
                              className="file-path-link"
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                onOpenFile(relativePath);
                              }}
                              title={`Open ${relativePath} in editor`}
                              {...props}
                            >
                              {children}
                            </code>
                          );
                        }
                      }
                      return <code className={className} {...props}>{children}</code>;
                    },
                    img: ({ src, alt, ...props }) => {
                      if (src && looksLikeFilePath(src)) {
                        const relativePath = toRelativePath(src);
                        if (isImagePath(relativePath)) {
                          return (
                            <InlineFilePathImage
                              path={relativePath}
                              originalText={src}
                              alt={alt}
                              onOpenFile={onOpenFile}
                            />
                          );
                        }
                      }
                      return <img src={src} alt={alt} {...props} />;
                    }
                  }}
                />
              </div>
            )}
            </div>
            {isUser && msg.status && (
              <div className="flex items-center gap-1 mt-1 mr-1">
                {msg.status === 'pending' && (
                  <span title="Sending...">
                    <Clock size={12} className="text-[var(--text-muted)]" />
                  </span>
                )}
                {msg.status === 'confirmed' && (
                  <span title="Delivered">
                    <Check size={12} className="text-emerald-500" />
                  </span>
                )}
                {msg.status === 'complete' && (
                  <span title="Processed">
                    <CheckCheck size={12} className="text-emerald-500" />
                  </span>
                )}
                {msg.status === 'failed' && (
                  <span title="Failed to send">
                    <AlertCircle size={12} className="text-red-500" />
                  </span>
                )}
                {msg.status === 'injected' && (
                  <span title="Sent mid-stream" className="flex items-center gap-1">
                    <Check size={12} className="text-amber-500" />
                    <span className="text-[10px] text-amber-500">mid-stream</span>
                  </span>
                )}
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}, (prev, next) => {
  // Custom comparison - only re-render when these specific props change
  return (
    prev.msg.content === next.msg.content &&
    prev.msg.status === next.msg.status &&
    prev.msg.isStreaming === next.msg.isStreaming &&
    prev.msg.isError === next.msg.isError &&
    prev.msg.formData?.status === next.msg.formData?.status &&
    prev.msg.blocks === next.msg.blocks &&
    prev.msg.reactions === next.msg.reactions &&
    prev.isContinuation === next.isContinuation &&
    prev.isEditing === next.isEditing &&
    prev.editText === next.editText &&
    prev.status === next.status &&
    prev.agentDisplayName === next.agentDisplayName
  );
});

interface ChatProps {
  isMobile?: boolean;
  onOpenFile?: (path: string) => void;
  // Multi-panel chat support
  claudeHook?: ClaudeHook;                        // External hook instance for secondary panel
  panelId?: string;                                 // 'primary' | 'secondary' — namespaces tab localStorage
  onSplitChat?: (sessionId: string) => void;        // Bubble up "open in split" action
  onPopoutChat?: (sessionId: string) => void;       // Bubble up "open in new window" action
  onCloseSplit?: () => void;                         // Close this split panel
  isSecondary?: boolean;                             // Visual hint for secondary panel
  // Bubble up to App so it can switch the right panel into salons mode and
  // focus the freshly created salon.
  onPromotedToSalon?: (salonId: string) => void;
}

export const Chat: React.FC<ChatProps> = ({
  isMobile = false,
  onOpenFile,
  claudeHook,
  panelId = 'primary',
  onSplitChat,
  onPopoutChat,
  onCloseSplit,
  isSecondary = false,
  onPromotedToSalon,
}) => {
  // Per-session message drafts — composer text is remembered per room.
  // Namespaced by panelId for split view independence.
  const draftsKey = panelId === 'primary' ? CHAT_DRAFTS_KEY : `${CHAT_DRAFTS_KEY}_${panelId}`;
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    try {
      const stored = localStorage.getItem(draftsKey);
      return stored ? JSON.parse(stored) : {};
    } catch { return {}; }
  });
  // Persist drafts
  useEffect(() => {
    try {
      localStorage.setItem(draftsKey, JSON.stringify(drafts));
    } catch { /* ignore */ }
  }, [drafts, draftsKey]);
  const [view, setView] = useState<'chat' | 'history'>('chat');
  const [historyList, setHistoryList] = useState<any[]>([]);
  // Ref mirror for access inside stable callbacks without re-binding.
  const historyDataRef = useRef<any[]>([]);
  useEffect(() => { historyDataRef.current = historyList; }, [historyList]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [reactionMsgId, setReactionMsgId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<{ name: string, path: string }[]>([]);
  const [imageAttachments, setImageAttachments] = useState<(ChatImageRef & { previewUrl?: string })[]>([]);
  const [showSearch, setShowSearch] = useState(false);
  const [showPromoteModal, setShowPromoteModal] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const { showToast } = useToast();
  const imageInputRef = useRef<HTMLInputElement>(null);

  // Chat tabs state — namespaced by panelId for split view independence
  const chatTabsKey = panelId === 'primary' ? CHAT_TABS_KEY : `${CHAT_TABS_KEY}_${panelId}`;
  const [chatTabs, setChatTabs] = useState<ChatTab[]>(() => {
    try {
      const stored = localStorage.getItem(chatTabsKey);
      return stored ? JSON.parse(stored) : [];
    } catch { return []; }
  });
  const [unreadSessions, setUnreadSessions] = useState<Set<string>>(new Set());

  // Persist chat tabs to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(chatTabsKey, JSON.stringify(chatTabs));
    } catch { /* ignore */ }
  }, [chatTabs, chatTabsKey]);

  // Sync unread state into tabs
  useEffect(() => {
    if (unreadSessions.size === 0) return;
    setChatTabs(prev => prev.map(tab => ({
      ...tab,
      hasUnread: unreadSessions.has(tab.sessionId)
    })));
  }, [unreadSessions]);

  // Helper: add or update a tab (auto-evicts oldest if > MAX_TABS)
  const upsertTab = useCallback((sessionId: string, title?: string, agent?: string) => {
    if (!sessionId || sessionId === 'new') return;
    setChatTabs(prev => {
      const existing = prev.find(t => t.sessionId === sessionId);
      if (existing) {
        // Update title/agent if provided
        if (title || agent) {
          return prev.map(t => t.sessionId === sessionId
            ? { ...t, title: title || t.title, agent: agent || t.agent, lastActivity: Date.now() }
            : t
          );
        }
        // Just bump lastActivity
        return prev.map(t => t.sessionId === sessionId
          ? { ...t, lastActivity: Date.now() }
          : t
        );
      }
      // Add new tab
      const newTab: ChatTab = {
        sessionId,
        title: title || 'New Chat',
        agent,
        hasUnread: false,
        lastActivity: Date.now(),
      };
      const updated = [...prev, newTab];
      // Evict oldest if over limit
      if (updated.length > MAX_CHAT_TABS) {
        updated.sort((a, b) => b.lastActivity - a.lastActivity);
        return updated.slice(0, MAX_CHAT_TABS);
      }
      return updated;
    });
  }, []);

  // Chess game state
  const chessGame = useChessGame();


  // Ref to hold loadChat function (avoids circular dependency)
  const loadChatRef = useRef<((id: string, agentHint?: string | null) => Promise<void>) | null>(null);

  // Callback for scheduled task completion - just refresh history
  // (Toast/sound/flash is handled by handleNewMessageNotification)
  const handleScheduledTaskComplete = useCallback((_data: { session_id: string; title: string }) => {
    // Refresh history list if we're viewing it
    if (view === 'history') {
      fetch(`${API_URL}/chat/history`)
        .then(res => res.json())
        .then(data => setHistoryList(data.chats || []))
        .catch(() => {});
    }
  }, [view]);

  // Callback for chat title updates (from Titler agent)
  const handleChatTitleUpdate = useCallback((data: { session_id: string; title: string; confidence: number }) => {
    // Update the title in the history list if present
    setHistoryList(prev =>
      prev.map(chat =>
        chat.id === data.session_id ? { ...chat, title: data.title } : chat
      )
    );
    // Also update matching chat tab title
    setChatTabs(prev => prev.map(tab =>
      tab.sessionId === data.session_id ? { ...tab, title: data.title } : tab
    ));
  }, []);

  // Callback for new chat creation (real-time history list update)
  const handleChatCreated = useCallback((data: { chat: { id: string; title: string; updated: number; is_system: boolean; scheduled: boolean; agent?: string } }) => {
    if (data.chat.is_system) return; // Don't show system chats
    setHistoryList(prev => {
      if (prev.some(c => c.id === data.chat.id)) return prev; // Prevent duplicates
      return [data.chat, ...prev]; // Prepend (most recent first)
    });
  }, []);

  // Tab flashing state for notifications
  const [shouldFlashTab, setShouldFlashTab] = useState(false);
  useTabFlash({ enabled: shouldFlashTab, message: 'New message' });

  // Form requests are now handled directly in useClaude as inline messages
  // This callback is kept for logging/debugging only
  const handleFormRequest = useCallback((data: {
    formId: string;
    title: string;
    description?: string;
    fields: FormField[];
    prefill?: Record<string, any>;
  }) => {
    console.log('Form request received (inline):', data);
  }, []);

  // Chess game update handler
  const handleChessUpdate = useCallback((game: ChessGameState) => {
    console.log('Chess game update:', game?.id);
    chessGame.updateGame(game);
  }, [chessGame]);

  // Callback for new message notifications
  const handleNewMessageNotification = useCallback((data: NotificationData) => {
    // Start tab flashing if window not focused
    if (!document.hasFocus()) {
      setShouldFlashTab(true);
    }

    // Mark session as unread in tab bar
    setUnreadSessions(prev => new Set([...prev, data.chatId]));

    // Auto-add tab for the notifying session if not already tabbed.
    // Prefer the chat's real title (from the server); fall back to the
    // history list; only use preview as a last resort (for scheduled tasks
    // the first streamed message is often a tool-call marker, which made
    // for terrible tab labels).
    const fallbackFromHistory = historyDataRef.current.find(c => c.id === data.chatId)?.title;
    const tabTitle = data.title || fallbackFromHistory || data.preview.slice(0, 40);
    upsertTab(data.chatId, tabTitle);

    // Show toast notification
    showToast({
      type: 'notification',
      title: data.critical ? 'URGENT: New message needs your attention' : 'New message received',
      message: data.preview.slice(0, 100) + (data.preview.length > 100 ? '...' : ''),
      duration: data.critical ? 0 : 8000, // Critical stays until dismissed
      playSound: data.playSound,
      critical: data.critical,
      action: {
        label: 'View Chat',
        onClick: () => {
          loadChatRef.current?.(data.chatId);
          setView('chat');
          setShouldFlashTab(false);
        }
      }
    });
  }, [showToast, upsertTab]);

  // Stop tab flashing when window gains focus
  useEffect(() => {
    const handleFocus = () => setShouldFlashTab(false);
    window.addEventListener('focus', handleFocus);
    return () => window.removeEventListener('focus', handleFocus);
  }, []);

  // When an external hook is provided (secondary panel), we still call useClaude
  // (React requires hooks to be called unconditionally) but disable its WebSocket.
  // Then we use the external hook's values for all state.
  const ownClaude = useClaude(claudeHook ? {
    enabled: false,
    instanceId: panelId,
    suppressGlobalEvents: true,
  } : {
    instanceId: panelId,
    onScheduledTaskComplete: handleScheduledTaskComplete,
    onChatTitleUpdate: handleChatTitleUpdate,
    onChatCreated: handleChatCreated,
    onNewMessageNotification: handleNewMessageNotification,
    onFormRequest: handleFormRequest,
    onChessUpdate: handleChessUpdate,
  });
  const claude = claudeHook || ownClaude;
  const {
    messages,
    sendMessage,
    editMessage,
    updateFormMessage,
    regenerateMessage,
    stopGeneration,
    deleteChat,
    status,
    statusText,
    activeTools,
    startNewChat,
    loadChat,
    sessionId,
    connectionStatus,
    queuedMessages,
    dismissQueuedMessage,
    currentAgent,
    sendMessageWithAgent,
    todos,
    streamPhase,
    toggleReaction,
    sendSlashCommand,
  } = claude;

  // Keep ref updated
  loadChatRef.current = loadChat;

  // Derive current composer text from per-session drafts.
  // Empty drafts are pruned from the map to keep storage clean.
  const input = drafts[sessionId] ?? '';
  const setInput = useCallback((value: string) => {
    setDrafts(prev => {
      if (!value) {
        if (!(sessionId in prev)) return prev;
        const next = { ...prev };
        delete next[sessionId];
        return next;
      }
      if (prev[sessionId] === value) return prev;
      return { ...prev, [sessionId]: value };
    });
  }, [sessionId]);

  // Agent selection state
  const { agents, getAgent } = useAgents();
  const [selectedAgentName, setSelectedAgentName] = useState<string | null>(null);

  // Slash command registry (fetched from server)
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([]);
  // Notice lifecycle: notices auto-fade after 5s and hide after 6s.
  // Tracked locally — notices are not persisted server-side.
  const [fadingNotices, setFadingNotices] = useState<Set<string>>(() => new Set());
  const [hiddenNotices, setHiddenNotices] = useState<Set<string>>(() => new Set());
  // Schedule fade/hide for any newly arrived notice
  useEffect(() => {
    const now = Date.now();
    const timeouts: number[] = [];
    for (const msg of messages) {
      if (msg.role !== 'notice') continue;
      if (fadingNotices.has(msg.id) || hiddenNotices.has(msg.id)) continue;
      // If notice has a timestamp older than 6s (e.g. came in via subscribe before
      // a refresh), hide immediately. Otherwise schedule fade.
      const age = msg.timestamp ? now - msg.timestamp : 0;
      if (age > 6000) {
        setHiddenNotices(prev => new Set(prev).add(msg.id));
        continue;
      }
      const fadeDelay = Math.max(0, 5000 - age);
      const hideDelay = Math.max(0, 6000 - age);
      timeouts.push(window.setTimeout(() => {
        setFadingNotices(prev => new Set(prev).add(msg.id));
      }, fadeDelay));
      timeouts.push(window.setTimeout(() => {
        setHiddenNotices(prev => new Set(prev).add(msg.id));
      }, hideDelay));
    }
    return () => { timeouts.forEach(t => clearTimeout(t)); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/slash-commands`)
      .then(r => r.ok ? r.json() : { commands: [] })
      .then(data => {
        if (!cancelled && Array.isArray(data?.commands)) {
          setSlashCommands(data.commands);
        }
      })
      .catch(err => console.warn('Failed to load slash commands:', err));
    return () => { cancelled = true; };
  }, []);
  // New chats have no default — user must select an agent (via UI or by typing the name).
  const effectiveAgentName: string | null = currentAgent || selectedAgentName || null;
  const selectedAgentObj = effectiveAgentName ? getAgent(effectiveAgentName) : undefined;
  const agentDisplayName = selectedAgentObj?.display_name || 'Select agent';

  // Reset chess game when session changes (new chat or loading different chat)
  useEffect(() => {
    chessGame.resetGame();
  }, [sessionId]);

  // Flag: scroll to bottom on next messages render (set when switching conversations)
  const scrollOnLoad = useRef(false);
  useEffect(() => {
    scrollOnLoad.current = true;
  }, [sessionId]);

  // Auto-add tab when sessionId transitions from 'new' to a real ID (new chat created)
  // Also clear unread when switching to a session
  const prevSessionId = useRef<string>(sessionId);
  useEffect(() => {
    if (sessionId !== 'new' && prevSessionId.current !== sessionId) {
      upsertTab(sessionId, undefined, effectiveAgentName ?? undefined);
      // Clear unread for the session we're now viewing
      setUnreadSessions(prev => {
        const next = new Set(prev);
        next.delete(sessionId);
        return next;
      });
    }
    prevSessionId.current = sessionId;
  }, [sessionId, upsertTab, effectiveAgentName]);

  // Handle ?chat= URL parameter on mount (for push notification deep links)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const chatId = params.get('chat');
    if (chatId && loadChatRef.current) {
      loadChatRef.current(chatId);
      // Clean up URL without reload
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  // Listen for service worker notification click messages
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === 'NOTIFICATION_CLICK' && event.data.chatId) {
        loadChatRef.current?.(event.data.chatId);
        setView('chat');
        setShouldFlashTab(false);
      }
    };
    navigator.serviceWorker?.addEventListener('message', handleMessage);
    return () => {
      navigator.serviceWorker?.removeEventListener('message', handleMessage);
    };
  }, []);

  // Ref for sendMessage to avoid recreating the listener on every render
  const sendMessageRef = useRef(sendMessage);
  sendMessageRef.current = sendMessage;

  // Listen for Brain App Bridge messages from iframes
  useEffect(() => {
    const handleBrainMessage = async (event: MessageEvent) => {
      // Verify it's a brain bridge message
      if (!event.data?.type?.startsWith('brain:')) return;

      console.log('[Brain Bridge] Received message:', event.data.type, event.data);
      const source = event.source as Window;

      if (event.data.type === 'brain:writeFile') {
        console.log('[Brain Bridge] Processing writeFile:', event.data.path);
        try {
          const res = await fetch(`${API_URL}/app-bridge/write`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: event.data.path, data: event.data.data })
          });
          console.log('[Brain Bridge] writeFile response:', res.status, res.statusText);
          if (!res.ok) {
            const errorText = await res.text();
            throw new Error(`Write failed: ${res.status} ${res.statusText} - ${errorText}`);
          }
          source?.postMessage({ type: 'brain:writeFileResponse', success: true }, '*');
          console.log('[Brain Bridge] writeFile success, response sent');
        } catch (err) {
          console.error('[Brain Bridge] writeFile error:', err);
          source?.postMessage({
            type: 'brain:writeFileResponse',
            success: false,
            error: err instanceof Error ? err.message : 'Write failed'
          }, '*');
        }
      }

      if (event.data.type === 'brain:readFile') {
        console.log('[Brain Bridge] Processing readFile:', event.data.path);
        try {
          const res = await fetch(`${API_URL}/app-bridge/read?path=${encodeURIComponent(event.data.path)}`);
          console.log('[Brain Bridge] readFile response:', res.status, res.statusText);
          if (!res.ok) {
            throw new Error(`Failed to read: ${res.status} ${res.statusText}`);
          }
          const content = await res.text();
          source?.postMessage({
            type: 'brain:readFileResponse',
            path: event.data.path,
            success: true,
            content
          }, '*');
          console.log('[Brain Bridge] readFile success, content length:', content.length);
        } catch (err) {
          console.error('[Brain Bridge] readFile error:', err);
          source?.postMessage({
            type: 'brain:readFileResponse',
            path: event.data.path,
            success: false,
            error: err instanceof Error ? err.message : 'Read failed'
          }, '*');
        }
      }

      if (event.data.type === 'brain:promptClaude') {
        console.log('[Brain Bridge] Processing promptClaude');
        // Fire-and-forget: send the prompt as a user message (v1 compat)
        sendMessageRef.current(event.data.prompt);
      }

      // --- Brain Bridge v2: askClaude (request-response) ---
      if (event.data.type === 'brain:askClaude') {
        const { prompt, requestId, options } = event.data;
        console.log('[Brain Bridge v2] Processing askClaude:', requestId);
        try {
          const res = await fetch(`${API_URL}/app-bridge/ask-claude`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt, system_hint: options?.systemHint })
          });
          if (!res.ok) {
            const errorText = await res.text();
            throw new Error(`askClaude failed: ${res.status} - ${errorText}`);
          }
          const data = await res.json();
          source?.postMessage({
            type: 'brain:askClaudeResponse',
            requestId,
            success: true,
            response: data.response
          }, '*');
          console.log('[Brain Bridge v2] askClaude success, response length:', data.response?.length);
        } catch (err) {
          console.error('[Brain Bridge v2] askClaude error:', err);
          source?.postMessage({
            type: 'brain:askClaudeResponse',
            requestId,
            success: false,
            error: err instanceof Error ? err.message : 'askClaude failed'
          }, '*');
        }
      }

      // --- Brain Bridge v2: askAgent (route through named agent) ---
      if (event.data.type === 'brain:askAgent') {
        const { agent, prompt, requestId } = event.data;
        console.log('[Brain Bridge v2] Processing askAgent:', agent, requestId);
        try {
          const res = await fetch(`${API_URL}/app-bridge/ask-agent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent, prompt }),
          });
          if (!res.ok) {
            const errorText = await res.text();
            throw new Error(`askAgent failed: ${res.status} - ${errorText}`);
          }
          const data = await res.json();
          source?.postMessage({
            type: 'brain:askAgentResponse',
            requestId,
            success: true,
            response: data.response
          }, '*');
          console.log('[Brain Bridge v2] askAgent success, response length:', data.response?.length);
        } catch (err) {
          console.error('[Brain Bridge v2] askAgent error:', err);
          source?.postMessage({
            type: 'brain:askAgentResponse',
            requestId,
            success: false,
            error: err instanceof Error ? err.message : 'askAgent failed'
          }, '*');
        }
      }

      // --- Brain Bridge v2: listFiles ---
      if (event.data.type === 'brain:listFiles') {
        const { dirPath } = event.data;
        console.log('[Brain Bridge v2] Processing listFiles:', dirPath);
        try {
          const res = await fetch(`${API_URL}/app-bridge/list?dirPath=${encodeURIComponent(dirPath || '')}`);
          if (!res.ok) throw new Error(`listFiles failed: ${res.status}`);
          const data = await res.json();
          source?.postMessage({
            type: 'brain:listFilesResponse',
            success: true,
            files: data.files
          }, '*');
        } catch (err) {
          console.error('[Brain Bridge v2] listFiles error:', err);
          source?.postMessage({
            type: 'brain:listFilesResponse',
            success: false,
            error: err instanceof Error ? err.message : 'listFiles failed'
          }, '*');
        }
      }

      // --- Brain Bridge v2: deleteFile ---
      if (event.data.type === 'brain:deleteFile') {
        const { path } = event.data;
        console.log('[Brain Bridge v2] Processing deleteFile:', path);
        try {
          const res = await fetch(`${API_URL}/app-bridge/delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
          });
          if (!res.ok) {
            const errorText = await res.text();
            throw new Error(`deleteFile failed: ${res.status} - ${errorText}`);
          }
          source?.postMessage({ type: 'brain:deleteFileResponse', success: true }, '*');
        } catch (err) {
          console.error('[Brain Bridge v2] deleteFile error:', err);
          source?.postMessage({
            type: 'brain:deleteFileResponse',
            success: false,
            error: err instanceof Error ? err.message : 'deleteFile failed'
          }, '*');
        }
      }

      // --- Brain Bridge v2: watchFile ---
      if (event.data.type === 'brain:watchFile') {
        const { path, intervalMs, watchId } = event.data;
        const interval = intervalMs || 2000;
        console.log('[Brain Bridge v2] Processing watchFile:', path, 'interval:', interval, 'watchId:', watchId);
        let lastMtime = 0;

        const poll = async () => {
          try {
            const statRes = await fetch(`${API_URL}/app-bridge/stat?path=${encodeURIComponent(path)}`);
            if (!statRes.ok) return;
            const stat = await statRes.json();
            if (stat.mtime !== lastMtime) {
              lastMtime = stat.mtime;
              // File changed — read content and push to iframe
              const readRes = await fetch(`${API_URL}/app-bridge/read?path=${encodeURIComponent(path)}`);
              if (readRes.ok) {
                const content = await readRes.text();
                source?.postMessage({
                  type: 'brain:fileChanged',
                  watchId,
                  path,
                  content,
                  mtime: stat.mtime
                }, '*');
              }
            }
          } catch (err) {
            console.error('[Brain Bridge v2] watchFile poll error:', err);
          }
        };

        // Initial read
        poll();
        const timerId = window.setInterval(poll, interval);

        // Store the timer so unwatchFile can clear it
        if (!(window as any).__brainWatchers) (window as any).__brainWatchers = {};
        (window as any).__brainWatchers[watchId] = timerId;

        source?.postMessage({ type: 'brain:watchFileResponse', watchId, success: true }, '*');
      }

      // --- Brain Bridge v2: unwatchFile ---
      if (event.data.type === 'brain:unwatchFile') {
        const { watchId } = event.data;
        console.log('[Brain Bridge v2] Processing unwatchFile:', watchId);
        const watchers = (window as any).__brainWatchers;
        if (watchers && watchers[watchId]) {
          window.clearInterval(watchers[watchId]);
          delete watchers[watchId];
        }
        source?.postMessage({ type: 'brain:unwatchFileResponse', watchId, success: true }, '*');
      }

      // --- Brain Bridge v2: getTheme (send current interface theme to iframe) ---
      if (event.data.type === 'brain:getTheme') {
        const root = document.documentElement;
        const style = getComputedStyle(root);
        source?.postMessage({
          type: 'brain:theme',
          mode: root.getAttribute('data-theme') || 'light',
          accent: style.getPropertyValue('--accent-primary').trim() || '#D97757',
          accentHover: style.getPropertyValue('--accent-hover').trim() || '#C4684A',
        }, '*');
      }

      // --- Brain Bridge v2: getAppInfo ---
      if (event.data.type === 'brain:getAppInfo') {
        console.log('[Brain Bridge v2] Processing getAppInfo');
        try {
          const res = await fetch(`${API_URL}/apps`);
          if (!res.ok) throw new Error(`getAppInfo failed: ${res.status}`);
          const apps = await res.json();
          // Try to identify which app is asking based on the iframe's current HTML file
          // The caller doesn't know its own entry path, so we pass the full registry
          source?.postMessage({
            type: 'brain:getAppInfoResponse',
            success: true,
            appInfo: { apps, currentEntry: null }
          }, '*');
        } catch (err) {
          console.error('[Brain Bridge v2] getAppInfo error:', err);
          source?.postMessage({
            type: 'brain:getAppInfoResponse',
            success: false,
            error: err instanceof Error ? err.message : 'getAppInfo failed'
          }, '*');
        }
      }
    };

    window.addEventListener('message', handleBrainMessage);
    console.log('[Brain Bridge v2] Message listener registered');
    return () => {
      window.removeEventListener('message', handleBrainMessage);
      console.log('[Brain Bridge v2] Message listener removed');
    };
  }, []); // Empty deps - listener is stable, uses ref for sendMessage

  const scrollRef = useRef<HTMLDivElement>(null);
  const historyListRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isUserNearBottom = useRef(true);
  useCodeBlockWrap(scrollRef);

  // Auto-resize textarea — preserve chat scroll position during reflow
  useEffect(() => {
    const textarea = textareaRef.current;
    const scrollEl = scrollRef.current;
    if (textarea) {
      // Snapshot scroll position before the resize reflow
      const prevScrollTop = scrollEl?.scrollTop ?? 0;

      textarea.style.height = 'auto';
      const maxHeight = window.innerHeight * 0.5;
      textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px';

      // Restore scroll position so typing never moves the chat
      if (scrollEl) {
        scrollEl.scrollTop = prevScrollTop;
      }
    }
  }, [input]);

  // Track whether user has scrolled away from the bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const handleScroll = () => {
      const threshold = 150; // px from bottom to count as "near bottom"
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      isUserNearBottom.current = distanceFromBottom <= threshold;
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // Keep isUserNearBottom fresh during DOM mutations that don't fire scroll events
  // (e.g., thinking block expand/collapse, tool blocks rendering).
  // Critical: if user WAS near bottom and content grew, keep them pinned to bottom.
  // This prevents tool call blocks from breaking auto-scroll (they increase DOM height
  // without a scroll event, which would otherwise flip isUserNearBottom to false).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const inner = el.firstElementChild as HTMLElement;
    if (!inner) return;
    const observer = new ResizeObserver(() => {
      if (isUserNearBottom.current) {
        // User was at/near bottom — keep them pinned as content grows
        el.scrollTop = el.scrollHeight;
        // isUserNearBottom stays true
      } else {
        // User has scrolled away — just recalculate in case they scrolled back
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        isUserNearBottom.current = distFromBottom <= 150;
      }
    });
    observer.observe(inner);
    return () => observer.disconnect();
  }, []);

  // Track content at the end of messages to detect actual new content
  // (vs. state-only changes like done handler marking blocks complete)
  const prevScrollAnchor = useRef({ lastId: '', contentLen: 0 });

  // Auto-scroll only when new content appears at the bottom AND user hasn't scrolled away.
  // Keyed on [messages] only — status changes (thinking→idle) should never trigger scroll.
  // Also handles one-time scroll-to-bottom when opening a conversation.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || messages.length === 0) {
      prevScrollAnchor.current = { lastId: '', contentLen: 0 };
      return;
    }

    // One-time scroll to bottom when a conversation is first loaded
    if (scrollOnLoad.current) {
      scrollOnLoad.current = false;
      // Use rAF so the DOM has rendered the messages before we measure scrollHeight
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
        isUserNearBottom.current = true;
      });
      // Update anchor so subsequent content checks work correctly
      const lastMsg = messages[messages.length - 1];
      prevScrollAnchor.current = {
        lastId: lastMsg.id,
        contentLen: lastMsg.blocks
          ? lastMsg.blocks.reduce((sum, b) => sum + (b.content?.length || 0), 0)
          : (lastMsg.content?.length || 0),
      };
      return;
    }

    const lastMsg = messages[messages.length - 1];
    const lastId = lastMsg.id;
    const contentLen = lastMsg.blocks
      ? lastMsg.blocks.reduce((sum, b) => sum + (b.content?.length || 0), 0)
      : (lastMsg.content?.length || 0);

    const prev = prevScrollAnchor.current;
    const hasNewContent = lastId !== prev.lastId || contentLen > prev.contentLen;
    prevScrollAnchor.current = { lastId, contentLen };

    if (hasNewContent && isUserNearBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  // Load history
  useEffect(() => {
    if (view === 'history') {
      setHistoryLoading(true);
      fetch(`${API_URL}/chat/history`)
        .then(res => res.json())
        .then(data => {
          setHistoryList(data.chats || []);
          setHistoryLoading(false);
          // Reset scroll AFTER the populated list has rendered. Two rAFs guarantees
          // the browser has laid out the new content before we set scrollTop.
          // (One rAF can fire before layout on some renders; two is the safe pattern.)
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              if (historyListRef.current) {
                historyListRef.current.scrollTop = 0;
              }
            });
          });
        })
        .catch(err => {
          console.error(err);
          setHistoryLoading(false);
        });
    }
  }, [view]);

  // Reset scroll to top when the history view opens AND when the list size
  // changes (i.e. when the fetched data arrives and the list grows from empty
  // to populated). The view-only dep was insufficient: the initial reset fired
  // on an empty/short container, then the browser's overflow-anchor pushed the
  // viewport down as items streamed in. useLayoutEffect runs synchronously
  // after DOM commit so this catches the populated render.
  useLayoutEffect(() => {
    if (view === 'history' && historyListRef.current) {
      historyListRef.current.scrollTop = 0;
    }
  }, [view, historyList.length]);

  const handleLoad = (id: string, title?: string, agent?: string) => {
    loadChat(id, agent || null);
    setView('chat');
    // Add a tab for the loaded chat
    upsertTab(id, title, agent);
    // Clear unread for this session
    setUnreadSessions(prev => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  // Chat tab handlers
  const handleTabClick = useCallback((tabSessionId: string) => {
    if (tabSessionId === sessionId) return; // Already active
    // Bug fix: Pass the agent from tab data so it displays immediately
    // instead of briefly showing the previous tab's agent
    const tab = chatTabs.find(t => t.sessionId === tabSessionId);
    loadChat(tabSessionId, tab?.agent || null);
    // Reset local selectedAgentName to prevent stale state from leaking across tabs
    setSelectedAgentName(null);
    setView('chat');
    // Clear unread for this session
    setUnreadSessions(prev => {
      const next = new Set(prev);
      next.delete(tabSessionId);
      return next;
    });
  }, [sessionId, loadChat, chatTabs]);

  const handleTabClose = useCallback((tabSessionId: string) => {
    setChatTabs(prev => {
      const filtered = prev.filter(t => t.sessionId !== tabSessionId);
      // If closing the active tab, switch to adjacent tab or start new chat
      if (tabSessionId === sessionId) {
        const closedIdx = prev.findIndex(t => t.sessionId === tabSessionId);
        if (filtered.length > 0) {
          // Switch to the tab that was to the left, or the first tab
          const nextTab = filtered[Math.min(closedIdx, filtered.length - 1)];
          loadChat(nextTab.sessionId, nextTab.agent || null);
        } else {
          startNewChat();
        }
      }
      return filtered;
    });
    // Clean up unread state
    setUnreadSessions(prev => {
      const next = new Set(prev);
      next.delete(tabSessionId);
      return next;
    });
  }, [sessionId, loadChat, startNewChat]);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm('Delete this conversation?')) {
      const success = await deleteChat(id);
      if (success) {
        setHistoryList(prev => prev.filter(c => c.id !== id));
      }
    }
  };

  const startEdit = (msg: ChatMessage) => {
    setEditingId(msg.id);
    setEditText(msg.content);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditText('');
  };

  const saveEdit = () => {
    if (editingId && editText.trim()) {
      editMessage(editingId, editText);
    }
    setEditingId(null);
    setEditText('');
  };

  const handleRegenerate = (msgId: string) => {
    regenerateMessage(msgId);
  };

  // Handle inline form submission
  const handleFormSubmit = useCallback((formId: string, values: Record<string, any>) => {
    // Update the form message to show submitted state
    updateFormMessage(formId, values);

    // Format submission as a structured message
    const formattedAnswers = Object.entries(values)
      .map(([key, value]) => `- **${key}**: ${value}`)
      .join('\n');

    const submissionMessage = `[FORM_SUBMISSION: ${formId}]\n${formattedAnswers}`;

    // Send as user message
    sendMessage(submissionMessage);
  }, [updateFormMessage, sendMessage]);

  // Try to auto-select an agent by matching a typed name against the agent list.
  // Returns true if an agent was matched and selected (input should be cleared).
  const tryAutoSelectAgent = useCallback((candidate: string): boolean => {
    const trimmed = candidate.trim();
    if (!trimmed) return false;
    const lower = trimmed.toLowerCase();
    const match = agents.find(a =>
      a.name.toLowerCase() === lower ||
      (a.display_name && a.display_name.toLowerCase() === lower)
    );
    if (match) {
      setSelectedAgentName(match.name);
      return true;
    }
    return false;
  }, [agents]);

  // Handle input changes.
  // On a fresh new chat with no agent selected, typing an agent name followed by
  // a space will auto-select that agent and clear the input (the typed name is
  // consumed as the selection, not part of the message).
  const handleInputChange = useCallback((value: string) => {
    if (!effectiveAgentName && messages.length === 0 && value.endsWith(' ')) {
      const prefix = value.slice(0, -1);
      if (tryAutoSelectAgent(prefix)) {
        setInput('');
        return;
      }
    }
    setInput(value);
  }, [setInput, effectiveAgentName, messages.length, tryAutoSelectAgent]);

  // Handle @mention autocomplete selection
  const handleMentionSelect = useCallback((agentName: string, replaceFrom: number) => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const cursorPos = textarea.selectionStart;
    const before = input.slice(0, replaceFrom);
    const after = input.slice(cursorPos);
    const newValue = `${before}@${agentName} ${after}`;
    setInput(newValue);
    // Set cursor position after the inserted mention
    requestAnimationFrame(() => {
      const newPos = replaceFrom + agentName.length + 2; // @name + space
      textarea.selectionStart = newPos;
      textarea.selectionEnd = newPos;
      textarea.focus();
    });
  }, [input, setInput]);

  // Upload image files to the server and return image refs
  const uploadImages = useCallback(async (files: File[]): Promise<ChatImageRef[]> => {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files', file);
    }
    try {
      const res = await fetch(`${API_URL}/chat/images`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }
      const data = await res.json();
      return data.images || [];
    } catch (err) {
      console.error('Image upload failed:', err);
      showToast({ type: 'warning', title: 'Upload failed', message: String(err) });
      return [];
    }
  }, [showToast]);

  // Add images to staging area (upload first, then preview)
  const stageImages = useCallback(async (files: File[]) => {
    // Filter to allowed types
    const allowed = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    const validFiles = files.filter(f => allowed.includes(f.type));
    if (validFiles.length === 0) return;

    // Upload to server
    const refs = await uploadImages(validFiles);
    if (refs.length === 0) return;

    // Create preview URLs and add to state
    const withPreviews = refs.map((ref, i) => ({
      ...ref,
      previewUrl: URL.createObjectURL(validFiles[i]),
    }));
    setImageAttachments(prev => [...prev, ...withPreviews]);
  }, [uploadImages]);

  // Handle image paste (Ctrl+V)
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault(); // Don't paste image as text
      stageImages(imageFiles);
    }
  }, [stageImages]);

  // Handle image file input change
  const handleImageSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) {
      stageImages(files);
    }
    // Reset input so same file can be selected again
    if (imageInputRef.current) imageInputRef.current.value = '';
  }, [stageImages]);

  // Remove an image from staging
  const removeImage = useCallback((id: string) => {
    setImageAttachments(prev => {
      const removed = prev.find(img => img.id === id);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter(img => img.id !== id);
    });
  }, []);

  // Slash command dispatcher — runs server-side directly, bypassing the agent.
  const dispatchSlash = useCallback((command: string, args: Record<string, any>) => {
    if (sessionId === 'new') {
      showToast({
        type: 'warning',
        title: 'No active chat',
        message: 'Send a message first to create a chat, then use slash commands.',
      });
      return;
    }
    const ok = sendSlashCommand(command, args);
    if (ok) {
      setInput('');
    } else {
      showToast({
        type: 'warning',
        title: 'Slash command failed',
        message: 'Could not send command. Connection may be down.',
      });
    }
  }, [sessionId, sendSlashCommand, setInput, showToast]);

  // Insert just the command name (with trailing space) so the user can keep typing args
  const handleInsertSlashName = useCallback((name: string) => {
    setInput(`/${name} `);
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (ta) {
        ta.focus();
        const pos = name.length + 2;
        ta.setSelectionRange(pos, pos);
      }
    });
  }, [setInput]);

  // Handle send
  const handleSend = useCallback(() => {
    if (!input.trim() && attachments.length === 0 && imageAttachments.length === 0) return;

    // ── Slash command interception ──
    // If input starts with `/`, parse and dispatch as slash command (bypasses agent).
    const trimmed = input.trim();
    if (trimmed.startsWith('/') && trimmed.length > 1 && trimmed[1] !== '/' && trimmed[1] !== ' ') {
      const parts = trimmed.slice(1).split(/\s+/);
      const commandName = parts[0];
      const positional = parts.slice(1);
      const cmd = slashCommands.find(c => c.name === commandName);
      if (cmd) {
        // Build args from positional values using the command's arg schema
        const args: Record<string, any> = {};
        for (let i = 0; i < positional.length && i < cmd.args.length; i++) {
          const arg = cmd.args[i];
          const raw = positional[i];
          if (arg.type === 'integer') {
            const n = parseInt(raw, 10);
            args[arg.name] = isNaN(n) ? raw : n;
          } else if (arg.type === 'boolean') {
            args[arg.name] = ['true', 'yes', '1', 'on'].includes(raw.toLowerCase());
          } else {
            args[arg.name] = raw;
          }
        }
        dispatchSlash(commandName, args);
        return;
      }
      // Unknown command — show toast, don't send to agent
      showToast({
        type: 'warning',
        title: 'Unknown command',
        message: `No such slash command: /${commandName}. Type /help to see available commands.`,
      });
      return;
    }

    // If no agent selected yet and the input is exactly an agent name, treat the
    // Enter press as a selection (not a send).
    if (!effectiveAgentName && !attachments.length && !imageAttachments.length) {
      if (tryAutoSelectAgent(input)) {
        setInput('');
        return;
      }
      showToast({
        type: 'warning',
        title: 'Select an agent',
        message: 'Pick an agent above, or type a name (e.g. "character ") before sending.',
      });
      return;
    }

    if (!effectiveAgentName) {
      showToast({
        type: 'warning',
        title: 'Select an agent',
        message: 'Pick an agent above before sending.',
      });
      return;
    }

    let fullMessage = input;
    if (attachments.length > 0) {
      const contextStr = attachments.map(a => `[CONTEXT: ${a.path}]`).join('\n');
      fullMessage = `${contextStr}\n${input}`;
    }

    // Strip preview URLs before sending (server doesn't need blob URLs)
    const imageRefs: ChatImageRef[] | undefined = imageAttachments.length > 0
      ? imageAttachments.map(({ previewUrl, ...ref }) => ref)
      : undefined;

    // If sending only images with no text, add a placeholder so the server doesn't skip it
    if (!fullMessage.trim() && imageRefs && imageRefs.length > 0) {
      fullMessage = `[Sent ${imageRefs.length} image${imageRefs.length > 1 ? 's' : ''}]`;
    }

    if (sendMessageWithAgent(fullMessage, effectiveAgentName, imageRefs)) {
      setInput('');
      setAttachments([]);
      // Clean up preview URLs
      imageAttachments.forEach(img => {
        if (img.previewUrl) URL.revokeObjectURL(img.previewUrl);
      });
      setImageAttachments([]);
    }
  }, [input, attachments, imageAttachments, sendMessageWithAgent, effectiveAgentName, setInput, tryAutoSelectAgent, showToast]);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();

    // Check for image files dropped from desktop
    if (e.dataTransfer.files?.length > 0) {
      const imageFiles: File[] = [];
      for (let i = 0; i < e.dataTransfer.files.length; i++) {
        const file = e.dataTransfer.files[i];
        if (file.type.startsWith('image/')) {
          imageFiles.push(file);
        }
      }
      if (imageFiles.length > 0) {
        stageImages(imageFiles);
        return;
      }
    }

    // Check for Second Brain file tree drops (existing behavior)
    const data = e.dataTransfer.getData('application/x-secondbrain-file');
    if (data) {
      try {
        const file = JSON.parse(data);
        if (!attachments.find(a => a.path === file.path)) {
          setAttachments(prev => [...prev, file]);
        }
      } catch (err) {
        console.error("Failed to parse dropped file", err);
      }
    }
  };

  const removeAttachment = (path: string) => {
    setAttachments(prev => prev.filter(a => a.path !== path));
  };

  // Ticker to cycle fun phrases during initializing/stalled phases
  const [_phraseTick, setPhraseTick] = useState(0);
  useEffect(() => {
    if (streamPhase === 'initializing' || streamPhase === 'stalled') {
      const interval = setInterval(() => setPhraseTick(t => t + 1), 2500);
      return () => clearInterval(interval);
    }
  }, [streamPhase]);

  const getStatusDisplay = (): string => {
    // For tool_use status with active tools, rendering is handled per-tool in JSX
    if (status === 'tool_use' && activeTools.size > 0) {
      return `Running ${activeTools.size} tool${activeTools.size > 1 ? 's' : ''}...`;
    }
    // Use statusText if explicitly set by server (e.g. tool names), but ignore generic "Thinking..."
    if (statusText && statusText !== 'Thinking...') return statusText;
    // Phase-aware fun phrases
    if (streamPhase === 'initializing') {
      return INITIAL_PHRASES[Math.floor(Date.now() / 2500) % INITIAL_PHRASES.length];
    }
    if (streamPhase === 'stalled') {
      return STALL_PHRASES[Math.floor(Date.now() / 3000) % STALL_PHRASES.length];
    }
    // During active streaming — no status text needed (content is flowing)
    if (streamPhase === 'streaming') return '';
    if (status === 'thinking' || status === 'processing') {
      return INITIAL_PHRASES[Math.floor(Date.now() / 2500) % INITIAL_PHRASES.length];
    }
    return '';
  };

  // Process messages: for block-based messages render blocks directly,
  // for legacy messages group tool_call messages with preceding assistant
  interface ProcessedMessage {
    message: ChatMessage;
    legacyToolCalls: ToolCallData[];
  }

  const processedMessages = useMemo<ProcessedMessage[]>(() => {
    const result: ProcessedMessage[] = [];
    // Buffer for tool_calls that couldn't attach to a preceding assistant.
    // These will attach to the NEXT assistant message instead.
    let pendingToolCalls: ToolCallData[] = [];

    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      if (msg.role === 'system') continue;

      // New format: has blocks — render directly, no grouping needed
      if (msg.blocks && msg.blocks.length > 0) {
        // Attach any buffered legacy tool_calls to this block-based message
        // (unlikely but handles mixed formats gracefully)
        if (pendingToolCalls.length > 0) {
          result.push({ message: msg, legacyToolCalls: pendingToolCalls });
          pendingToolCalls = [];
        } else {
          result.push({ message: msg, legacyToolCalls: [] });
        }
        continue;
      }

      // Old format: group tool_call messages with nearest assistant
      if ((msg as any).role === 'tool_call') {
        const tc = msg as unknown as ToolCallMessage;
        const toolCallData: ToolCallData = {
          id: tc.id,
          tool_name: tc.tool_name,
          tool_id: tc.tool_id,
          args: tc.args || {},
          output_summary: tc.output_summary,
          is_error: tc.is_error,
        };
        // Try to attach to preceding assistant message
        const last = result[result.length - 1];
        if (last && last.message.role === 'assistant') {
          last.legacyToolCalls.push(toolCallData);
        } else {
          // Buffer for the next assistant message
          pendingToolCalls.push(toolCallData);
        }
        continue;
      }

      // Skip hidden non-tool messages (ping mode wake-up triggers)
      if (msg.hidden) continue;

      // If this is an assistant message and we have buffered tool_calls, attach them
      if (msg.role === 'assistant' && pendingToolCalls.length > 0) {
        result.push({ message: msg, legacyToolCalls: pendingToolCalls });
        pendingToolCalls = [];
      } else {
        result.push({ message: msg, legacyToolCalls: [] });
      }
    }

    return result;
  }, [messages]);


  // Connection status indicator
  const ConnectionIndicator = () => (
    <div className="flex items-center gap-1.5">
      <div className={clsx(
        "w-2 h-2 rounded-full",
        connectionStatus === 'connected' && "bg-emerald-500",
        connectionStatus === 'connecting' && "bg-amber-500 animate-pulse",
        connectionStatus === 'disconnected' && "bg-red-500"
      )} />
      <span className="text-xs text-[var(--text-muted)] hidden sm:inline">
        {connectionStatus === 'connected' ? 'Connected' :
         connectionStatus === 'connecting' ? 'Connecting...' : 'Disconnected'}
      </span>
    </div>
  );


  // History View
  if (view === 'history') {
    return (
      <div className="flex flex-col h-full bg-[var(--bg-primary)]">
        {/* Header */}
        <div className="h-14 border-b border-[var(--border-color)] flex items-center px-4 bg-[var(--bg-secondary)] shadow-warm">
          <button
            onClick={() => setView('chat')}
            className="p-2 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-secondary)] transition-colors"
          >
            <ChevronLeft size={20} />
          </button>
          <span className="font-semibold text-[var(--text-primary)] ml-2">Conversations</span>
          <button
            onClick={() => setShowSearch(true)}
            className="ml-auto p-2 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-secondary)] transition-colors"
            title="Search conversations"
          >
            <Search size={20} />
          </button>
        </div>

        {/* Search Overlay */}
        {showSearch && (
          <ChatSearch
            onSelectResult={(chatId, _messageId) => {
              handleLoad(chatId, undefined, undefined);
              setShowSearch(false);
              // Note: _messageId available for future scroll-to-message feature
            }}
            onClose={() => setShowSearch(false)}
          />
        )}

        {/* History List */}
        <div ref={historyListRef} className="flex-1 overflow-y-auto p-4" style={{ overflowAnchor: 'none' }}>
          {historyLoading && historyList.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-[var(--text-muted)]">
              <Loader2 size={32} className="mb-3 animate-spin opacity-50" />
              <p className="text-sm">Loading conversations...</p>
            </div>
          ) : historyList.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-[var(--text-muted)]">
              <MessageCircle size={48} strokeWidth={1.5} className="mb-3 opacity-50" />
              <p className="text-sm">No conversations yet</p>
            </div>
          ) : (
            <div className="space-y-2 max-w-2xl mx-auto">
              {historyList.map(chat => (
                <div
                  key={chat.id}
                  onClick={() => handleLoad(chat.id, chat.title, chat.agent)}
                  className="p-4 bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-color)] hover:border-[var(--accent-primary)] hover:shadow-warm-lg cursor-pointer transition-all group"
                >
                  <div className="flex justify-between items-start">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="font-medium text-[var(--text-primary)] truncate group-hover:text-[var(--accent-primary)] transition-colors">
                          {chat.title}
                        </div>
                        {chat.agent && chat.agent !== 'character' && chat.agent !== 'claudey' && (() => {
                          const chatAgentObj = getAgent(chat.agent);
                          const ChatAgentIcon = getAgentIcon(chatAgentObj?.icon);
                          return (
                            <span
                              className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-full shrink-0 text-white"
                              style={{ backgroundColor: 'var(--accent-primary)' }}
                            >
                              <ChatAgentIcon size={10} />
                              {chatAgentObj?.display_name || chat.agent}
                            </span>
                          );
                        })()}
                        {chat.is_system && (
                          <span className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-full shrink-0" style={{ backgroundColor: 'var(--accent-light)', color: 'var(--accent-primary)' }}>
                            <Clock size={10} />
                            Scheduled
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-[var(--text-muted)] mt-1">
                        {new Date(chat.updated * 1000).toLocaleDateString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit'
                        })}
                      </div>
                    </div>
                    <button
                      onClick={(e) => handleDelete(chat.id, e)}
                      className="p-2 opacity-0 group-hover:opacity-100 hover:bg-red-50 hover:text-red-500 text-[var(--text-muted)] rounded-lg transition-all dark:hover:bg-red-900/20"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  // Chat View
  return (
    <div className="flex flex-col h-full bg-[var(--bg-primary)]">
      {/* Header */}
      <div className="h-14 border-b border-[var(--border-color)] flex items-center justify-between px-4 bg-[var(--bg-secondary)] shadow-warm">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setView('history')}
            className="p-2 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-secondary)] transition-colors"
            title="History"
          >
            <History size={20} />
          </button>
          <AgentSelector
            agents={agents}
            selectedAgent={selectedAgentObj}
            currentChatAgent={currentAgent || (messages.length > 0 ? effectiveAgentName : null)}
            onSelect={(agent) => setSelectedAgentName(agent.name)}
          />
          <MoodSelector agentName={effectiveAgentName || selectedAgentObj?.name} />
        </div>

        <div className="flex items-center gap-3">
          <ConnectionIndicator />
          {/* Chess button - only show if game exists */}
          {chessGame.game && (
            <button
              onClick={chessGame.openGame}
              className={clsx(
                "p-2 rounded-lg transition-colors",
                chessGame.game.game_over
                  ? "text-[var(--text-muted)] hover:bg-[var(--bg-tertiary)]"
                  : "text-[var(--accent-primary)] hover:bg-[var(--accent-light)]"
              )}
              title={chessGame.game.game_over ? "View completed game" : "Open chess game"}
            >
              <Crown size={18} />
            </button>
          )}
          {/* Promote to salon — only meaningful for an existing 1:1 chat */}
          {sessionId && sessionId !== 'new' && messages.length > 0 && effectiveAgentName && (
            <button
              onClick={() => setShowPromoteModal(true)}
              className="p-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
              title="Promote to salon (add another agent)"
            >
              <Users size={18} />
            </button>
          )}
          <button
            onClick={startNewChat}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
          >
            <Plus size={16} />
            <span className="hidden sm:inline">New</span>
          </button>
        </div>
      </div>

      {/* Chat Tabs */}
      {(chatTabs.length > 0 || isSecondary) && (
        <ChatTabBar
          tabs={chatTabs}
          activeSessionId={sessionId}
          onTabClick={handleTabClick}
          onTabClose={handleTabClose}
          getAgent={getAgent}
          onContextAction={(action, tabSessionId) => {
            if (action === 'split') {
              onSplitChat?.(tabSessionId);
            } else if (action === 'popout') {
              onPopoutChat?.(tabSessionId);
            } else if (action === 'closeOthers') {
              setChatTabs(prev => prev.filter(t => t.sessionId === tabSessionId));
            }
          }}
          isSecondary={isSecondary}
          onCloseSplit={onCloseSplit}
        />
      )}

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto" onClick={(e) => {
        // Dismiss mobile reaction picker when tapping the scroll background (not a message)
        if (isMobile && reactionMsgId && e.target === e.currentTarget) setReactionMsgId(null);
      }}>
        <div className="max-w-[1440px] mx-auto px-4 py-6 space-y-6">
          {processedMessages.length === 0 && (() => {
            const EmptyIcon = getAgentIcon(selectedAgentObj?.icon);
            return (
              <div className="flex flex-col items-center justify-center h-[60vh] text-[var(--text-muted)]">
                <div
                  className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4"
                  style={{ backgroundColor: 'var(--accent-primary)', opacity: 0.9 }}
                >
                  <EmptyIcon size={32} className="text-white" />
                </div>
                <p className="text-lg font-medium text-[var(--text-secondary)] mb-1">How can I help?</p>
                <p className="text-sm text-[var(--text-muted)]">
                  {selectedAgentObj?.description || 'Start a conversation or ask me anything'}
                </p>
              </div>
            );
          })()}

          {processedMessages.map(({ message: msg, legacyToolCalls }, idx) => {
            const isUser = msg.role === 'user';
            const hasBlocks = msg.blocks && msg.blocks.length > 0;

            // Notice messages (slash command results, system events) — render
            // as a small centered chip in the timeline.
            if (msg.role === 'notice') {
              if (hiddenNotices.has(msg.id)) return null;
              const noticeKind = msg.kind;
              const isFading = fadingNotices.has(msg.id);
              // Compact, single-line pill. Title and content joined with " — " when both exist.
              const pillText = msg.title && msg.content
                ? `${msg.title} — ${msg.content}`
                : (msg.content || msg.title || '');
              return (
                <div
                  key={msg.id}
                  className={clsx(
                    "flex justify-center my-1.5 transition-opacity duration-1000",
                    isFading ? "opacity-0" : "opacity-100"
                  )}
                >
                  <div
                    className={clsx(
                      "rounded-full border px-2.5 py-0.5 text-[10.5px] flex items-center gap-1.5 max-w-[80%]",
                      msg.ok === false
                        ? "border-red-300/50 bg-red-50/60 text-red-700 dark:border-red-700/40 dark:bg-red-900/10 dark:text-red-300"
                        : noticeKind === "noop"
                          ? "border-amber-300/40 bg-amber-50/40 text-amber-700/90 dark:border-amber-700/30 dark:bg-amber-900/10 dark:text-amber-300/90"
                          : "border-[var(--border-color)]/60 bg-[var(--bg-secondary)]/60 text-[var(--text-muted)]"
                    )}
                  >
                    <Sparkles size={10} className="flex-shrink-0 opacity-60" />
                    <span className="truncate">{pillText}</span>
                  </div>
                </div>
              );
            }

            // Skip form submission messages - the InlineForm already shows the summary
            if (isUser && msg.content.startsWith('[FORM_SUBMISSION:')) {
              return null;
            }

            // Skip scheduled automation trigger messages - only show the assistant response
            if (isUser && msg.content.includes('[SCHEDULED AUTOMATION]')) {
              return null;
            }

            // Skip empty assistant messages (no blocks and no content)
            if (!isUser && !hasBlocks && msg.isStreaming && !msg.content.trim()) {
              return null;
            }

            // For block-based messages, skip if all blocks are empty in-progress
            if (!isUser && hasBlocks) {
              const hasVisibleContent = msg.blocks!.some(b =>
                b.content.trim() || b.type === 'tool_use' || b.type === 'tool_result' || b.type === 'thinking'
              );
              if (!hasVisibleContent && msg.isStreaming) {
                return null;
              }
            }

            // Walk backwards past hidden messages (form submissions, scheduled triggers,
            // empty streaming) to find the actual previous VISIBLE message for header logic
            let prevMsg: typeof processedMessages[0] | null = null;
            for (let pi = idx - 1; pi >= 0; pi--) {
              const pm = processedMessages[pi];
              const pmUser = pm.message.role === 'user';
              const pmHasBlocks = pm.message.blocks && pm.message.blocks.length > 0;
              // Same skip conditions as above
              if (pmUser && pm.message.content.startsWith('[FORM_SUBMISSION:')) continue;
              if (pmUser && pm.message.content.includes('[SCHEDULED AUTOMATION]')) continue;
              if (!pmUser && !pmHasBlocks && pm.message.isStreaming && !pm.message.content.trim()) continue;
              if (!pmUser && pmHasBlocks) {
                const hasVis = pm.message.blocks!.some(b =>
                  b.content.trim() || b.type === 'tool_use' || b.type === 'tool_result' || b.type === 'thinking'
                );
                if (!hasVis && pm.message.isStreaming) continue;
              }
              prevMsg = pm;
              break;
            }

            return (
              <React.Fragment key={msg.id}>
                {hasBlocks ? (
                  // Block-based rendering (new format)
                  (() => {
                    const mentionAgent = msg.agent;
                    const mentionAgentObj = mentionAgent ? getAgent(mentionAgent) : null;
                    const MentionIcon = mentionAgent ? getAgentIcon(mentionAgentObj?.icon) : null;
                    const mentionDisplayName = mentionAgentObj?.display_name || (mentionAgent ? `@${mentionAgent}` : null);
                    // Agent messages always show header (never treated as continuation)
                    const isContinuation = !isUser && !mentionAgent && prevMsg?.message.role === 'assistant' && !prevMsg?.message.agent;
                    // Typing indicator for @mentioned agents
                    const isAgentTyping = mentionAgent && msg.isStreaming && msg.status === 'processing';

                    return (
                      <div
                        className={clsx(
                          "group flex flex-col gap-1",
                          isUser ? "items-end" : "items-start w-full",
                          mentionAgent && "pl-3 border-l-2 border-[var(--accent-primary)]/40"
                        )}
                        onClick={() => { if (isMobile && !isUser) setReactionMsgId(prev => prev === msg.id ? null : msg.id); }}
                      >
                        {/* Header — agent name, only if not continuation */}
                        {!isContinuation && (
                          <div className="flex items-center gap-2 mb-1">
                            {mentionAgent && MentionIcon && (
                              <div className="w-5 h-5 rounded-md flex items-center justify-center" style={{ backgroundColor: 'var(--accent-primary)' }}>
                                <MentionIcon size={12} className="text-white" />
                              </div>
                            )}
                            <span className={clsx("text-xs font-medium", mentionAgent ? "text-[var(--accent-primary)]" : "text-[var(--text-muted)]")}>
                              {isUser ? 'You' : (mentionDisplayName || agentDisplayName)}
                            </span>
                          </div>
                        )}

                        {/* Agent typing indicator */}
                        {isAgentTyping ? (
                          <div className="flex items-center gap-2 py-2 px-1">
                            <div className="flex items-center gap-1">
                              <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-primary)] animate-bounce" style={{ animationDelay: '0ms' }} />
                              <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-primary)] animate-bounce" style={{ animationDelay: '150ms' }} />
                              <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-primary)] animate-bounce" style={{ animationDelay: '300ms' }} />
                            </div>
                            <span className="text-xs text-[var(--text-muted)]">{mentionDisplayName || `@${mentionAgent}`} is thinking...</span>
                          </div>
                        ) : (
                          /* Render blocks individually with appropriate wrappers */
                          <BlockRenderer blocks={msg.blocks!} onOpenFile={onOpenFile} />
                        )}

                        {/* Actions row: reactions, copy, regenerate — all inline */}
                        {!msg.isStreaming && (
                          <div className={clsx("flex items-center gap-1 mt-0.5", isUser ? "justify-end" : "justify-start")}>
                            {(!isUser || (msg.reactions && Object.keys(msg.reactions).length > 0)) && (
                              <ReactionBar msg={msg} onToggle={(emoji) => { toggleReaction(msg.id, emoji); setReactionMsgId(null); }} isActive={reactionMsgId === msg.id} readOnly={isUser} alignRight={isUser} />
                            )}
                            {!msg.formData && status === 'idle' && (
                              <CopyButton msg={msg} isUser={isUser} />
                            )}
                            {!isUser && msg.role === 'assistant' && !mentionAgent && idx === processedMessages.length - 1 && status === 'idle' && (
                              <button
                                onClick={() => handleRegenerate(msg.id)}
                                className="p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-all opacity-0 group-hover:opacity-60 hover:!opacity-100 focus:opacity-100 flex items-center gap-1 text-xs"
                                title="Regenerate"
                              >
                                <RotateCcw size={14} />
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })()
                ) : (
                  // Legacy rendering
                  (() => {
                    const legacyMentionAgent = msg.agent;
                    const legacyMentionObj = legacyMentionAgent ? getAgent(legacyMentionAgent) : null;
                    const LegacyMentionIcon = legacyMentionAgent ? getAgentIcon(legacyMentionObj?.icon) : null;
                    const legacyMentionName = legacyMentionObj?.display_name || (legacyMentionAgent ? `@${legacyMentionAgent}` : null);
                    const legacyIsContinuation = !isUser && !legacyMentionAgent && prevMsg?.message.role === 'assistant' && !prevMsg?.message.agent;

                    return (
                  <div
                    className={clsx("group", isUser && "flex flex-col items-end", legacyMentionAgent && "pl-3 border-l-2 border-[var(--accent-primary)]/40")}
                    onClick={() => { if (isMobile && !isUser) setReactionMsgId(prev => prev === msg.id ? null : msg.id); }}
                  >
                    {/* Agent header for @mentioned agents in legacy mode */}
                    {legacyMentionAgent && !legacyIsContinuation && (
                      <div className="flex items-center gap-2 mb-1">
                        {LegacyMentionIcon && (
                          <div className="w-5 h-5 rounded-md flex items-center justify-center" style={{ backgroundColor: 'var(--accent-primary)' }}>
                            <LegacyMentionIcon size={12} className="text-white" />
                          </div>
                        )}
                        <span className="text-xs font-medium text-[var(--accent-primary)]">
                          {legacyMentionName}
                        </span>
                      </div>
                    )}
                    {/* Legacy tool chips ABOVE message text (chronological order: tools ran before response) */}
                    {legacyToolCalls.length > 0 && !msg.isStreaming && (
                      <ToolCallChips toolCalls={legacyToolCalls} />
                    )}
                    <ChatMessageItem
                      msg={msg}
                      isUser={isUser}
                      isContinuation={legacyIsContinuation}
                      isEditing={editingId === msg.id}
                      editText={editText}
                      onEditTextChange={setEditText}
                      status={status}
                      agentDisplayName={legacyMentionName || agentDisplayName}
                      onStartEdit={startEdit}
                      onCancelEdit={cancelEdit}
                      onSaveEdit={saveEdit}
                      onFormSubmit={handleFormSubmit}
                      onOpenFile={onOpenFile}
                    />
                    {/* Actions row: copy, regenerate, reactions — all inline */}
                    {!msg.isStreaming && (
                      <div className={clsx("flex items-center gap-1 mt-0.5", isUser ? "justify-end" : "justify-start")}>
                        {(!isUser || (msg.reactions && Object.keys(msg.reactions).length > 0)) && (
                          <ReactionBar msg={msg} onToggle={(emoji) => { toggleReaction(msg.id, emoji); setReactionMsgId(null); }} isActive={reactionMsgId === msg.id} readOnly={isUser} alignRight={isUser} />
                        )}
                        {!msg.formData && status === 'idle' && (
                          <CopyButton msg={msg} isUser={isUser} />
                        )}
                        {!isUser && msg.role === 'assistant' && !legacyMentionAgent && idx === processedMessages.length - 1 && status === 'idle' && (
                          <button
                            onClick={() => handleRegenerate(msg.id)}
                            className="p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-all opacity-0 group-hover:opacity-60 hover:!opacity-100 focus:opacity-100 flex items-center gap-1 text-xs"
                            title="Regenerate"
                          >
                            <RotateCcw size={14} />
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                    );
                  })()
                )}

              </React.Fragment>
            );
          })}

          {/* Queued messages are now rendered in fixed strip above input area — see below */}

          {/* Status indicator — shows during initializing/stalled phases, hidden while content flows */}
          {status !== 'idle' && activeTools.size === 0 &&
           streamPhase !== 'streaming' &&
           !messages.some(m => m.isStreaming && m.blocks && m.blocks.length > 0 && m.blocks.some(b => b.type === 'text' && b.content.trim())) && (
            <div className="flex items-center gap-2 pl-11 animate-in">
              <Loader2 size={14} className="animate-spin" style={{ color: 'var(--accent-primary)' }} />
              <span className="text-xs text-[var(--text-muted)]">{getStatusDisplay()}</span>
            </div>
          )}
        </div>
      </div>

      {/* Todo Strip - shown when any agent uses TodoWrite */}
      {todos.length > 0 && (
        <div className="border-t border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 pt-3 pb-0">
          <div className="max-w-[1440px] mx-auto">
            <div className="flex flex-col gap-1">
              {todos.map((todo, idx) => {
                const isActive = todo.status === 'in_progress';
                const isDone = todo.status === 'completed';
                return (
                  <div
                    key={idx}
                    className={clsx(
                      "flex items-center gap-2 text-xs transition-all duration-300",
                      isDone && "opacity-50",
                      isActive && "font-medium"
                    )}
                  >
                    {/* Status icon */}
                    {isDone ? (
                      <Check size={13} className="text-green-500 flex-shrink-0" />
                    ) : isActive ? (
                      <Loader2 size={13} className="animate-spin flex-shrink-0" style={{ color: 'var(--accent-primary)' }} />
                    ) : (
                      <Circle size={13} className="text-[var(--text-muted)] flex-shrink-0" />
                    )}
                    {/* Task text */}
                    <span className={clsx(
                      "truncate",
                      isDone ? "text-[var(--text-muted)] line-through" :
                      isActive ? "text-[var(--text-primary)]" :
                      "text-[var(--text-muted)]"
                    )}>
                      {isActive && todo.activeForm ? todo.activeForm : todo.content}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Queued messages strip — fixed above input, visually distinct */}
      {queuedMessages.length > 0 && (
        <div className="border-t-2 border-amber-500/40 bg-amber-50/10 dark:bg-amber-900/10 px-4 py-2">
          <div className="max-w-[1440px] mx-auto space-y-2">
            {queuedMessages.map((qMsg) => (
              <div key={qMsg.id} className="flex items-center gap-3 animate-in">
                {/* Status icon */}
                <div className="flex-shrink-0">
                  {qMsg.status === 'pending' && (
                    <Clock size={16} className="text-amber-500 animate-pulse" />
                  )}
                  {qMsg.status === 'confirmed' && (
                    <Check size={16} className="text-emerald-500" />
                  )}
                  {qMsg.status === 'not_delivered' && (
                    <AlertCircle size={16} className="text-red-500" />
                  )}
                </div>

                {/* Message preview — compact inline */}
                <div className="flex-1 min-w-0 flex items-center gap-2">
                  <span className={clsx(
                    "text-sm truncate",
                    qMsg.status === 'not_delivered'
                      ? "text-red-400"
                      : "text-[var(--text-primary)]"
                  )} style={{ fontFamily: 'var(--font-chat)' }}>
                    {qMsg.content}
                  </span>
                  <span className={clsx(
                    "text-[11px] flex-shrink-0",
                    qMsg.status === 'pending' && "text-amber-500",
                    qMsg.status === 'confirmed' && "text-emerald-500",
                    qMsg.status === 'not_delivered' && "text-red-400"
                  )}>
                    {qMsg.status === 'pending' && 'queued'}
                    {qMsg.status === 'confirmed' && 'injected'}
                    {qMsg.status === 'not_delivered' && 'failed'}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1 flex-shrink-0">
                  {qMsg.status === 'not_delivered' && (
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(qMsg.content);
                        showToast({ type: 'notification', title: 'Copied to clipboard', duration: 2000 });
                      }}
                      className="p-1 hover:bg-[var(--bg-tertiary)] rounded text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
                      title="Copy message"
                    >
                      <Copy size={12} />
                    </button>
                  )}
                  <button
                    onClick={() => dismissQueuedMessage(qMsg.id)}
                    className="p-1 hover:bg-[var(--bg-tertiary)] rounded text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
                    title={qMsg.status === 'not_delivered' ? 'Dismiss' : 'Cancel'}
                  >
                    <X size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Input Area */}
      <div className="border-t border-[var(--border-color)] bg-[var(--bg-secondary)] p-4">
        <div className="max-w-[1440px] mx-auto">
          {/* Attachments */}
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {attachments.map(file => (
                <div
                  key={file.path}
                  className="flex items-center gap-2 px-3 py-1.5 bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-full text-sm text-[var(--text-secondary)]"
                >
                  <FileIcon size={14} style={{ color: 'var(--accent-primary)' }} />
                  <span className="max-w-[150px] truncate">{file.name}</span>
                  <button
                    onClick={() => removeAttachment(file.path)}
                    className="p-0.5 hover:bg-[var(--border-color)] rounded-full transition-colors"
                  >
                    <X size={14} className="text-[var(--text-muted)]" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Image attachments preview */}
          {imageAttachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {imageAttachments.map(img => (
                <div key={img.id} className="relative group">
                  <img
                    src={img.previewUrl || `${API_URL}/chat/images/${img.filename}`}
                    alt={img.originalName}
                    className="h-20 w-20 object-cover rounded-lg border border-[var(--border-color)]"
                  />
                  <button
                    onClick={() => removeImage(img.id)}
                    className="absolute -top-1.5 -right-1.5 p-0.5 bg-[var(--bg-primary)] border border-[var(--border-color)] rounded-full opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
                  >
                    <X size={12} className="text-[var(--text-muted)]" />
                  </button>
                  <div className="absolute bottom-0 left-0 right-0 bg-black/50 text-white text-[10px] px-1 py-0.5 rounded-b-lg truncate">
                    {img.originalName}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Queued messages are now shown in the fixed strip above */}

          {/* Hidden file input for image upload */}
          <input
            ref={imageInputRef}
            type="file"
            accept="image/jpeg,image/png,image/gif,image/webp"
            multiple
            className="hidden"
            onChange={handleImageSelect}
          />

          {/* Input box */}
          <div className="relative">
            {/* @mention autocomplete dropdown */}
            <MentionAutocomplete
              input={input}
              agents={agents}
              textareaRef={textareaRef}
              onSelect={handleMentionSelect}
            />

            {/* /slash command autocomplete dropdown */}
            <SlashCommandAutocomplete
              input={input}
              commands={slashCommands}
              textareaRef={textareaRef}
              onPickCommand={dispatchSlash}
              onInsertCommandName={handleInsertSlashName}
            />

            <div
              className={clsx(
                "flex items-end gap-3 bg-[var(--bg-tertiary)] border rounded-2xl p-3 input-focus",
                (attachments.length > 0 || imageAttachments.length > 0) ? "border-[var(--accent-primary)]" : "border-[var(--border-color)]",
                status !== 'idle' && input.trim() && "border-amber-400/50"
              )}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
            >
              {/* Image upload button */}
              <button
                onClick={() => imageInputRef.current?.click()}
                className="p-2 rounded-lg hover:bg-[var(--border-color)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors flex-shrink-0"
                title="Attach image (or paste with Ctrl+V)"
              >
                <ImagePlus size={18} />
              </button>

              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => handleInputChange(e.target.value)}
                onPaste={handlePaste}
                placeholder={status !== 'idle'
                  ? `Type a follow-up... (will queue until ${agentDisplayName} finishes)`
                  : effectiveAgentName
                    ? `Message ${agentDisplayName}...`
                    : `Type an agent name + space to select, or pick one above...`
                }
                rows={1}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    if (isMobile) {
                      // Mobile: Enter adds newline
                      return;
                    } else if (!e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  } else if (e.key === 'Tab' && !e.shiftKey) {
                    // Tab inserts an indent instead of shifting focus.
                    // Shift+Tab is left alone so it still escapes the textarea (accessibility).
                    e.preventDefault();
                    const textarea = e.currentTarget;
                    const start = textarea.selectionStart;
                    const end = textarea.selectionEnd;
                    const newValue = input.substring(0, start) + '\t' + input.substring(end);
                    handleInputChange(newValue);
                    // Restore cursor position after React flushes the new value.
                    requestAnimationFrame(() => {
                      textarea.selectionStart = textarea.selectionEnd = start + 1;
                    });
                  }
                }}
                className="flex-1 bg-transparent border-none text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:ring-0 focus:outline-none resize-none py-1.5 px-1 font-chat"
                style={{ minHeight: '24px', maxHeight: '150px', fontFamily: 'var(--font-chat)', fontSize: 'var(--font-size-base)' }}
              />

              {/* Stop button - always visible when Claude is working */}
              {status !== 'idle' && (
                <button
                  onClick={() => stopGeneration()}
                  className="p-2.5 rounded-xl transition-all flex-shrink-0 bg-red-500 hover:bg-red-600 text-white shadow-md"
                  title="Stop generating"
                >
                  <Square size={18} fill="currentColor" />
                </button>
              )}

              {/* Send/Queue button */}
              <button
                onClick={handleSend}
                disabled={!input.trim() && attachments.length === 0 && imageAttachments.length === 0}
                className={clsx(
                  "p-2.5 rounded-xl transition-all flex-shrink-0 btn-primary",
                  (!input.trim() && attachments.length === 0 && imageAttachments.length === 0)
                    ? "bg-[var(--bg-tertiary)] text-[var(--text-muted)] cursor-not-allowed border border-[var(--border-color)]"
                    : status !== 'idle'
                      ? "bg-amber-500 hover:bg-amber-600 text-white shadow-md"
                      : "text-white shadow-md"
                )}
                style={{
                  backgroundColor: (!input.trim() && attachments.length === 0 && imageAttachments.length === 0)
                    ? undefined
                    : status !== 'idle'
                      ? undefined // amber color handled by class
                      : 'var(--accent-primary)'
                }}
                title={status !== 'idle' ? "Queue message" : "Send message"}
              >
                {status !== 'idle' && (input.trim() || attachments.length > 0) ? (
                  <Clock size={18} />
                ) : (
                  <Send size={18} />
                )}
              </button>
            </div>
          </div>

          <p className="text-xs text-[var(--text-muted)] text-center mt-2">
            {status !== 'idle'
              ? "Press Enter to queue message · Shift+Enter for new line"
              : "Press Enter to send, Shift+Enter for new line"
            }
          </p>
        </div>
      </div>

      {/* Confirm Modal */}
      {/* Chess Game Modal */}
      {chessGame.isOpen && (
        <ChessGame
          game={chessGame.game}
          onClose={chessGame.closeGame}
          onMove={async (move) => {
            const claudePrompt = await chessGame.makeMove(move);
            // If it's Claude's turn, inject the position into the chat
            if (claudePrompt) {
              sendMessage(claudePrompt);
            }
          }}
          onNewGame={chessGame.startNewGame}
        />
      )}

      {/* Promote-to-salon Modal */}
      {showPromoteModal && effectiveAgentName && sessionId && sessionId !== 'new' && (
        <PromoteToSalonModal
          chatAgent={effectiveAgentName}
          agents={agents}
          submitting={promoting}
          onCancel={() => !promoting && setShowPromoteModal(false)}
          onPromote={async (newParticipant) => {
            setPromoting(true);
            try {
              const res = await promoteChatToSalon({
                chat_id: sessionId,
                participant: newParticipant,
              });
              setShowPromoteModal(false);
              showToast({ title: 'Promoted to salon', type: 'success' });
              onPromotedToSalon?.(res.salon_id);
            } catch (e: any) {
              showToast({ title: 'Promote failed', message: e?.message || String(e), type: 'warning' });
            } finally {
              setPromoting(false);
            }
          }}
        />
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Promote-to-Salon Modal
//
// Shows a list of agents (excluding the current chat's agent + zeke). Picking
// one calls POST /api/salons/promote-chat — backend copies the chat history
// into a new salon and brings the new agent in.
// ---------------------------------------------------------------------------

const PromoteToSalonModal: React.FC<{
  chatAgent: string;
  agents: ReturnType<typeof useAgents>['agents'];
  submitting: boolean;
  onCancel: () => void;
  onPromote: (newParticipant: string) => Promise<void>;
}> = ({ chatAgent, agents, submitting, onCancel, onPromote }) => {
  const candidates = useMemo(() => {
    return agents
      .filter((a) => a.chattable !== false)
      .filter((a) => a.name !== chatAgent && a.name !== 'user');
  }, [agents, chatAgent]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-[var(--bg-primary)] border border-[var(--border-color)] rounded-lg w-full max-w-sm p-4 space-y-3 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Promote to salon</h3>
          <button onClick={onCancel} className="p-1 hover:bg-[var(--bg-tertiary)] rounded" disabled={submitting}>
            <X size={14} />
          </button>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          This 1:1 chat with <strong>{chatAgent}</strong> will become a salon. Pick another agent to invite — the convener will take over routing.
        </p>
        <div className="border border-[var(--border-color)] rounded max-h-72 overflow-y-auto bg-[var(--bg-secondary)]">
          {candidates.length === 0 && (
            <div className="px-3 py-4 text-xs text-[var(--text-muted)] text-center">
              No other chattable agents available.
            </div>
          )}
          {candidates.map((a) => (
            <button
              key={a.name}
              disabled={submitting}
              onClick={() => onPromote(a.name)}
              className="w-full text-left px-3 py-2 hover:bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] flex items-center justify-between disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>{a.display_name || a.name}</span>
              <span className="text-[10px] text-[var(--text-muted)]">{a.name}</span>
            </button>
          ))}
        </div>
        {submitting && (
          <div className="text-[11px] text-[var(--text-muted)] text-center">Promoting…</div>
        )}
      </div>
    </div>
  );
};
