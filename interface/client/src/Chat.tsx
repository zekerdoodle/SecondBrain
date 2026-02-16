import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Send, Loader2, Plus, History, ChevronLeft, Pencil, RotateCcw, X,
  File as FileIcon, Trash2, MessageCircle, Sparkles, Clock, Square, Search,
  Check, CheckCheck, AlertCircle, Crown, ImagePlus, Circle, Copy
} from 'lucide-react';
import { ChatSearch } from './ChatSearch';
import { useClaude, type ClaudeHook, type NotificationData, type ChessGameState } from './useClaude';
import { useToast } from './Toast';
import { useTabFlash } from './hooks/useTabFlash';
import { useCodeBlockWrap } from './hooks/useCodeBlockWrap';
import { useAgents } from './hooks/useAgents';
import type { ChatMessage, ChatImageRef, FormField, ToolCallMessage } from './types';
import { clsx } from 'clsx';
import MDEditor from '@uiw/react-md-editor';
import { API_URL } from './config';
import { InlineForm } from './components/InlineForm';
import { ChessGame, useChessGame } from './components/ChessGame';
import { AgentSelector } from './components/AgentSelector';
import { ChatTabBar } from './components/ChatTabBar';
import { getAgentIcon } from './utils/agentIcons';
import { getToolDisplayName } from './utils/toolDisplay';
import { ToolCallChips, type ToolCallData } from './components/ToolCallChips';
import type { ChatTab } from './types';

// Accent color is now managed via CSS variables (--accent-primary)
const CHAT_TABS_KEY = 'second_brain_chat_tabs';
const MAX_CHAT_TABS = 8;

// Status phrases
const THINKING_PHRASES = ['Thinking...', 'Processing...', 'Working on it...'];


// --- File path detection for clickable links ---
// Matches paths that look like files in the Second Brain project tree
// Detects: absolute paths (/home/debian/...), relative project paths (interface/..., .claude/..., 00_Inbox/..., etc.)
const FILE_PATH_REGEX = /^(?:\/home\/debian\/second_brain\/)?(?:(?:interface|\.claude|0[0-5]_\w+|docs|20_Areas|10_Active_Projects|30_Incubator|40_Archive|05_App_Data)\/)[\w./_-]+\.\w{1,10}$/;

// Check if an inline code string looks like a file path
const looksLikeFilePath = (text: string): boolean => {
  // Must contain at least one slash and a file extension
  if (!text.includes('/') || !text.includes('.')) return false;
  // Must not contain spaces (paths in backticks shouldn't)
  if (text.includes(' ')) return false;
  // Must not be a URL
  if (/^https?:\/\//.test(text)) return false;
  // Match known project paths or relative paths with extensions
  return FILE_PATH_REGEX.test(text) || /^[\w./_-]+\/[\w./_-]+\.\w{1,10}$/.test(text);
};

// Strip the absolute prefix to get a relative project path
const toRelativePath = (path: string): string => {
  const prefix = '/home/debian/second_brain/';
  if (path.startsWith(prefix)) {
    return path.slice(prefix.length);
  }
  return path;
};

// --- Memoized Chat Message Component ---
// Prevents all messages from re-rendering when only the streaming message changes
interface ChatMessageProps {
  msg: ChatMessage;
  isUser: boolean;
  isLastAssistant: boolean;
  isContinuation: boolean;
  isEditing: boolean;
  editText: string;
  onEditTextChange: (text: string) => void;
  status: string;
  agentDisplayName: string;
  onStartEdit: (msg: ChatMessage) => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onRegenerate: (id: string) => void;
  onFormSubmit: (formId: string, values: Record<string, any>) => void;
  onOpenFile?: (path: string) => void;
}

const ChatMessageItem = React.memo<ChatMessageProps>(({
  msg, isUser, isLastAssistant, isContinuation, isEditing,
  editText, onEditTextChange, status, agentDisplayName,
  onStartEdit, onCancelEdit, onSaveEdit, onRegenerate, onFormSubmit, onOpenFile
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    const textToCopy = isUser
      ? msg.content.split('\n').filter(line => !line.startsWith('[CONTEXT:')).join('\n').trim()
      : msg.content;
    try {
      await navigator.clipboard.writeText(textToCopy);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Fallback for older browsers
      const textarea = document.createElement('textarea');
      textarea.value = textToCopy;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }, [msg.content, isUser]);

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
                "rounded-2xl px-4 py-3 text-[15px] leading-relaxed animate-in",
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
                      <div className="whitespace-pre-wrap font-chat" style={{ fontFamily: 'var(--font-chat)', fontSize: 'var(--font-size-base)' }}>
                        {displayText}
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
                  source={msg.content}
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
                      if (isInline && onOpenFile && looksLikeFilePath(text)) {
                        const relativePath = toRelativePath(text);
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
                      return <code className={className} {...props}>{children}</code>;
                    }
                  }}
                />
              </div>
            )}
            </div>
            {isUser && (msg.status || msg.injected) && (
              <div className="flex items-center gap-1 mt-1 mr-1">
                {msg.injected && (
                  <span title="Injected mid-stream" className="text-xs text-amber-500 mr-1">
                    ⚡
                  </span>
                )}
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
                {(msg.status === 'injected' || (!msg.status && msg.injected)) && (
                  <span title="Injected to active stream">
                    <Sparkles size={12} className="text-amber-500" />
                  </span>
                )}
                {msg.status === 'failed' && (
                  <span title="Failed to send">
                    <AlertCircle size={12} className="text-red-500" />
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        {/* Copy + Regenerate buttons */}
        {!msg.isStreaming && !msg.formData && status === 'idle' && (
          <div className={clsx("flex items-center gap-1 mt-1", isUser ? "justify-end" : "justify-start")}>
            <button
              onClick={handleCopy}
              className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-all flex items-center gap-1 text-xs opacity-0 group-hover:opacity-100 focus:opacity-100"
              title={copied ? "Copied!" : "Copy message"}
            >
              {copied ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
              {copied && <span className="text-emerald-500">Copied</span>}
            </button>
            {isLastAssistant && (
              <button
                onClick={() => onRegenerate(msg.id)}
                className="p-1.5 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors flex items-center gap-1 text-xs opacity-0 group-hover:opacity-100 focus:opacity-100"
                title="Regenerate"
              >
                <RotateCcw size={12} />
                <span>Regenerate</span>
              </button>
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
    prev.isLastAssistant === next.isLastAssistant &&
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
}) => {
  const [input, setInput] = useState('');
  const [view, setView] = useState<'chat' | 'history'>('chat');
  const [historyList, setHistoryList] = useState<any[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [attachments, setAttachments] = useState<{ name: string, path: string }[]>([]);
  const [imageAttachments, setImageAttachments] = useState<(ChatImageRef & { previewUrl?: string })[]>([]);
  const [showSearch, setShowSearch] = useState(false);
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

    // Auto-add tab for the notifying session if not already tabbed
    upsertTab(data.chatId, data.preview.slice(0, 40));

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
    clearQueuedMessages,
    currentAgent,
    sendMessageWithAgent,
    streamingToolMap,
    todos,
  } = claude;

  // Keep ref updated
  loadChatRef.current = loadChat;

  // Agent selection state
  const { agents, defaultAgent, getAgent } = useAgents();
  const [selectedAgentName, setSelectedAgentName] = useState<string | null>(null);
  const effectiveAgentName = currentAgent || selectedAgentName || defaultAgent?.name || 'ren';
  const selectedAgentObj = getAgent(effectiveAgentName);
  const agentDisplayName = selectedAgentObj?.display_name || 'Ren';

  // Reset chess game when session changes (new chat or loading different chat)
  useEffect(() => {
    chessGame.resetGame();
  }, [sessionId]);

  // Auto-add tab when sessionId transitions from 'new' to a real ID (new chat created)
  // Also clear unread when switching to a session
  const prevSessionId = useRef<string>(sessionId);
  useEffect(() => {
    if (sessionId !== 'new' && prevSessionId.current !== sessionId) {
      upsertTab(sessionId, undefined, effectiveAgentName);
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
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isUserNearBottom = useRef(true);
  useCodeBlockWrap(scrollRef);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      const maxHeight = window.innerHeight * 0.5;
      textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px';
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

  // Auto-scroll only when user is near the bottom
  useEffect(() => {
    if (scrollRef.current && isUserNearBottom.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, status]);

  // Load history
  useEffect(() => {
    if (view === 'history') {
      setHistoryLoading(true);
      fetch(`${API_URL}/chat/history`)
        .then(res => res.json())
        .then(data => {
          setHistoryList(data.chats || []);
          setHistoryLoading(false);
        })
        .catch(err => {
          console.error(err);
          setHistoryLoading(false);
        });
    }
  }, [view]);

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

  // Handle input changes
  const handleInputChange = useCallback((value: string) => {
    setInput(value);
  }, []);

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

  // Handle send
  const handleSend = useCallback(() => {
    if (!input.trim() && attachments.length === 0 && imageAttachments.length === 0) return;

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
  }, [input, attachments, imageAttachments, sendMessageWithAgent, effectiveAgentName]);

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

  const getStatusDisplay = (): string => {
    // For tool_use status with active tools, rendering is handled per-tool in JSX
    // This function is now only for non-tool or fallback states
    if (status === 'tool_use' && activeTools.size > 0) {
      // Shouldn't be called in this case, but fallback just in case
      return `Running ${activeTools.size} tool${activeTools.size > 1 ? 's' : ''}...`;
    }
    // For non-tool states, use statusText if available
    if (statusText) return statusText;
    if (status === 'thinking') {
      return THINKING_PHRASES[Math.floor(Date.now() / 2000) % THINKING_PHRASES.length];
    }
    if (status === 'processing') return 'Processing...';
    return '';
  };

  // Group messages: attach tool_call messages to the preceding assistant message
  // Tool calls are stored as { role: 'tool_call', hidden: true, ... } in the message array
  interface MessageGroup {
    message: ChatMessage;
    trailingToolCalls: ToolCallData[];
  }

  const messageGroups = useMemo<MessageGroup[]>(() => {
    const groups: MessageGroup[] = [];

    for (const msg of messages) {
      if (msg.role === 'system') continue;

      // Tool call messages — attach to preceding assistant message
      if ((msg as any).role === 'tool_call') {
        const tc = msg as unknown as ToolCallMessage;
        const lastGroup = groups[groups.length - 1];
        if (lastGroup && lastGroup.message.role === 'assistant') {
          lastGroup.trailingToolCalls.push({
            id: tc.id,
            tool_name: tc.tool_name,
            tool_id: tc.tool_id,
            args: tc.args || {},
            output_summary: tc.output_summary,
            is_error: tc.is_error,
          });
        }
        continue;
      }

      // Skip hidden non-tool messages (ping mode wake-up triggers)
      if (msg.hidden) continue;

      groups.push({ message: msg, trailingToolCalls: [] });
    }

    return groups;
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
        <div className="flex-1 overflow-y-auto p-4">
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
                        {chat.agent && chat.agent !== 'ren' && chat.agent !== 'claudey' && (() => {
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
                          <span className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium bg-orange-100 text-orange-700 rounded-full shrink-0 dark:bg-orange-900/30 dark:text-orange-400">
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
      {chatTabs.length > 0 && (
        <ChatTabBar
          tabs={chatTabs}
          activeSessionId={sessionId}
          onTabClick={handleTabClick}
          onTabClose={handleTabClose}
          onNewChat={startNewChat}
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
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
          {messageGroups.length === 0 && (() => {
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

          {messageGroups.map((group, idx) => {
            const { message: msg, trailingToolCalls } = group;
            const isUser = msg.role === 'user';

            // Skip form submission messages - the InlineForm already shows the summary
            if (isUser && msg.content.startsWith('[FORM_SUBMISSION:')) {
              return null;
            }

            // Skip scheduled automation trigger messages - only show the assistant response
            if (isUser && msg.content.includes('[SCHEDULED AUTOMATION]')) {
              return null;
            }

            const prevGroup = idx > 0 ? messageGroups[idx - 1] : null;

            return (
              <React.Fragment key={msg.id}>
                <ChatMessageItem
                  msg={msg}
                  isUser={isUser}
                  isLastAssistant={msg.role === 'assistant' && idx === messageGroups.length - 1}
                  isContinuation={!isUser && prevGroup?.message.role === 'assistant'}
                  isEditing={editingId === msg.id}
                  editText={editText}
                  onEditTextChange={setEditText}
                  status={status}
                  agentDisplayName={agentDisplayName}
                  onStartEdit={startEdit}
                  onCancelEdit={cancelEdit}
                  onSaveEdit={saveEdit}
                  onRegenerate={handleRegenerate}
                  onFormSubmit={handleFormSubmit}
                  onOpenFile={onOpenFile}
                />
                {trailingToolCalls.length > 0 && !msg.isStreaming && (
                  <ToolCallChips toolCalls={trailingToolCalls} />
                )}
                {/* Streaming tool chips: show tools that completed after this message was finalized */}
                {status !== 'idle' && streamingToolMap.get(msg.id) && (
                  <ToolCallChips toolCalls={streamingToolMap.get(msg.id)!} />
                )}
              </React.Fragment>
            );
          })}

          {/* Streaming tool chips for tools that fired before any text (rare, but handles edge case) */}
          {status !== 'idle' && streamingToolMap.get('__pre__') && (
            <ToolCallChips toolCalls={streamingToolMap.get('__pre__')!} />
          )}

          {/* Status indicator */}
          {status !== 'idle' && (
            <div className="flex flex-col gap-2 animate-in">
              {activeTools.size > 0 ? (
                Array.from(activeTools.values())
                  .sort((a, b) => a.startedAt - b.startedAt)
                  .map(tool => (
                    <div key={tool.id} className="flex items-start gap-3 animate-in">
                      <div
                        className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                        style={{ backgroundColor: 'var(--accent-light)' }}
                      >
                        <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent-primary)' }} />
                      </div>
                      <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-2xl rounded-bl-md px-4 py-3 shadow-warm">
                        <span className="text-sm text-[var(--text-muted)]">
                          {getToolDisplayName(tool.name)}
                          {tool.summary && (
                            <span className="opacity-60 ml-1.5">
                              {tool.summary}
                            </span>
                          )}
                        </span>
                      </div>
                    </div>
                  ))
              ) : (
                <div className="flex items-start gap-3">
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                    style={{ backgroundColor: 'var(--accent-light)' }}
                  >
                    <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent-primary)' }} />
                  </div>
                  <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-2xl rounded-bl-md px-4 py-3 shadow-warm">
                    <span className="text-sm text-[var(--text-muted)]">{getStatusDisplay()}</span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Injected messages - being sent mid-stream */}
          {queuedMessages.length > 0 && (
            <div className="space-y-3 mt-3">
              {queuedMessages.map((qMsg, idx) => (
                <div key={qMsg.id} className="flex flex-col items-end animate-in">
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-xs font-medium flex items-center gap-1 ${
                      qMsg.failed ? 'text-red-500' : 'text-amber-600 dark:text-amber-400'
                    }`}>
                      {qMsg.failed ? (
                        <>
                          <AlertCircle size={12} />
                          Failed to inject
                        </>
                      ) : (
                        <>
                          <Sparkles size={12} />
                          Injecting {queuedMessages.length > 1 ? `(${idx + 1}/${queuedMessages.length})` : ''}
                        </>
                      )}
                    </span>
                    <span className="text-xs text-[var(--text-muted)]">You</span>
                  </div>
                  <div className={`max-w-[75%] rounded-2xl px-4 py-3 text-[15px] leading-relaxed rounded-br-md border-2 border-dashed ${
                    qMsg.failed
                      ? 'border-red-400/50 bg-red-50/50 dark:bg-red-900/20'
                      : 'border-amber-400/50 bg-amber-50/50 dark:bg-amber-900/20'
                  } text-[var(--text-secondary)]`}>
                    <div className="whitespace-pre-wrap font-chat" style={{ fontFamily: 'var(--font-chat)', fontSize: 'var(--font-size-base)' }}>
                      {qMsg.content}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Todo Strip - shown when any agent uses TodoWrite */}
      {todos.length > 0 && (
        <div className="border-t border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 pt-3 pb-0">
          <div className="max-w-5xl mx-auto">
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

      {/* Input Area */}
      <div className="border-t border-[var(--border-color)] bg-[var(--bg-secondary)] p-4">
        <div className="max-w-5xl mx-auto">
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

          {/* Injected messages indicator */}
          {queuedMessages.length > 0 && (
            <div className="flex items-center justify-between mb-2 px-1">
              <div className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-400">
                <Sparkles size={14} />
                <span>
                  {queuedMessages.length} message{queuedMessages.length > 1 ? 's' : ''} being injected
                  <span className="text-[var(--text-muted)] ml-1">
                    — Claude will see {queuedMessages.length > 1 ? 'them' : 'it'} mid-stream
                  </span>
                </span>
              </div>
              <button
                onClick={clearQueuedMessages}
                className="text-xs text-[var(--text-muted)] hover:text-red-500 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}

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
                  : `Message ${agentDisplayName}...`
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
    </div>
  );
};
