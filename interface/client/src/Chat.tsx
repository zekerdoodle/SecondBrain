import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Send, Loader2, Plus, History, ChevronLeft, Pencil, RotateCcw, X,
  File as FileIcon, Trash2, MessageCircle, Sparkles, Clock, Square, Search,
  Check, CheckCheck, AlertCircle
} from 'lucide-react';
import { ChatSearch } from './ChatSearch';
import { useClaude, type NotificationData } from './useClaude';
import { useToast } from './Toast';
import { useTabFlash } from './hooks/useTabFlash';
import type { ChatMessage, FormField } from './types';
import { clsx } from 'clsx';
import MDEditor from '@uiw/react-md-editor';
import { API_URL } from './config';
import { InlineForm } from './components/InlineForm';
import { CommandPalette } from './components/CommandPalette';
import { ConfirmModal } from './components/ConfirmModal';
import {
  isCommandInput,
  getCommandQuery,
  parseCommand,
  type Command,
  type CommandContext,
  type CommandExecuteResult,
} from './utils/commands';

// Accent color is now managed via CSS variables (--accent-primary)

// Status phrases
const THINKING_PHRASES = ['Thinking...', 'Processing...', 'Working on it...'];
// Tool display name mapping - maps raw tool names to human-friendly display names
// Supports both simple names (Read, Write) and MCP-prefixed names (mcp__brain__invoke_agent)
// Add new tools here to customize their display in the UI
const TOOL_DISPLAY_NAMES: Record<string, string> = {
  // Claude Code built-in tools
  'Read': 'Reading file',
  'Write': 'Writing file',
  'Edit': 'Editing file',
  'Bash': 'Running command',
  'Glob': 'Searching files',
  'Grep': 'Searching content',
  'WebSearch': 'Searching the web',
  'WebFetch': 'Fetching page',
  'Task': 'Running task',
  'TodoWrite': 'Updating tasks',

  // MCP Brain tools - Agents
  'mcp__brain__invoke_agent': 'Invoking agent',
  'mcp__brain__invoke_agent_chain': 'Running agent chain',
  'mcp__brain__schedule_agent': 'Scheduling agent',

  // MCP Brain tools - Google Calendar/Tasks
  'mcp__brain__google_create_tasks_and_events': 'Creating task/event',
  'mcp__brain__google_list': 'Checking calendar/tasks',
  'mcp__brain__google_delete_task': 'Deleting task',
  'mcp__brain__google_update_task': 'Updating task',

  // MCP Brain tools - Gmail
  'mcp__brain__gmail_list_messages': 'Checking email',
  'mcp__brain__gmail_get_message': 'Reading email',
  'mcp__brain__gmail_send': 'Sending email',
  'mcp__brain__gmail_reply': 'Replying to email',
  'mcp__brain__gmail_list_labels': 'Listing labels',
  'mcp__brain__gmail_modify_labels': 'Modifying labels',
  'mcp__brain__gmail_trash': 'Moving to trash',
  'mcp__brain__gmail_draft_create': 'Creating draft',

  // MCP Brain tools - YouTube Music
  'mcp__brain__ytmusic_get_playlists': 'Getting playlists',
  'mcp__brain__ytmusic_get_playlist_items': 'Getting playlist items',
  'mcp__brain__ytmusic_get_liked': 'Getting liked songs',
  'mcp__brain__ytmusic_search': 'Searching YouTube Music',
  'mcp__brain__ytmusic_create_playlist': 'Creating playlist',
  'mcp__brain__ytmusic_add_to_playlist': 'Adding to playlist',
  'mcp__brain__ytmusic_remove_from_playlist': 'Removing from playlist',
  'mcp__brain__ytmusic_delete_playlist': 'Deleting playlist',

  // MCP Brain tools - Spotify
  'mcp__brain__spotify_auth_start': 'Starting Spotify auth',
  'mcp__brain__spotify_auth_callback': 'Completing Spotify auth',
  'mcp__brain__spotify_recently_played': 'Getting recent plays',
  'mcp__brain__spotify_top_items': 'Getting top items',
  'mcp__brain__spotify_search': 'Searching Spotify',
  'mcp__brain__spotify_get_playlists': 'Getting playlists',
  'mcp__brain__spotify_create_playlist': 'Creating playlist',
  'mcp__brain__spotify_add_to_playlist': 'Adding to playlist',
  'mcp__brain__spotify_now_playing': 'Checking Spotify',
  'mcp__brain__spotify_playback_control': 'Controlling playback',

  // MCP Brain tools - Scheduler
  'mcp__brain__schedule_self': 'Scheduling reminder',
  'mcp__brain__scheduler_list': 'Listing scheduled tasks',
  'mcp__brain__scheduler_update': 'Updating scheduled task',
  'mcp__brain__scheduler_remove': 'Removing scheduled task',

  // MCP Brain tools - Journal Memory
  'mcp__brain__memory_append': 'Saving to memory',
  'mcp__brain__memory_read': 'Reading memory',

  // MCP Brain tools - Working Memory
  'mcp__brain__working_memory_add': 'Adding note',
  'mcp__brain__working_memory_update': 'Updating note',
  'mcp__brain__working_memory_remove': 'Removing note',
  'mcp__brain__working_memory_list': 'Checking notes',
  'mcp__brain__working_memory_snapshot': 'Getting memory snapshot',

  // MCP Brain tools - Long-Term Memory (LTM)
  'mcp__brain__ltm_search': 'Searching memory',
  'mcp__brain__ltm_get_context': 'Getting context',
  'mcp__brain__ltm_add_memory': 'Storing memory',
  'mcp__brain__ltm_create_thread': 'Creating memory thread',
  'mcp__brain__ltm_stats': 'Getting memory stats',
  'mcp__brain__ltm_process_now': 'Processing memory',
  'mcp__brain__ltm_run_gardener': 'Running memory gardener',
  'mcp__brain__ltm_buffer_exchange': 'Exchanging memory buffer',
  'mcp__brain__ltm_backfill': 'Backfilling memory',

  // MCP Brain tools - Utilities
  'mcp__brain__page_parser': 'Reading page',
  'mcp__brain__restart_server': 'Restarting server',
  'mcp__brain__claude_code': 'Running Claude Code',
  'mcp__brain__web_search': 'Searching the web',
  'mcp__brain__send_critical_notification': 'Sending notification',

  // MCP Brain tools - Bash
  'mcp__brain__bash': 'Running command',

  // MCP Brain tools - Forms
  'mcp__brain__forms_define': 'Creating form',
  'mcp__brain__forms_show': 'Showing form',
  'mcp__brain__forms_save': 'Saving form',
  'mcp__brain__forms_list': 'Listing forms',

  // MCP Brain tools - Moltbook (AI social platform)
  'mcp__brain__moltbook_feed': 'Browsing Moltbook',
  'mcp__brain__moltbook_post': 'Posting to Moltbook',
  'mcp__brain__moltbook_comment': 'Commenting on Moltbook',
  'mcp__brain__moltbook_get_post': 'Reading Moltbook post',
  'mcp__brain__moltbook_notifications': 'Checking Moltbook notifications',
};

// Get a human-friendly display name for a tool
// First tries exact match, then falls back to substring matching
const getToolDisplayName = (toolName: string): string => {
  // First, try exact match (handles MCP tools with full names)
  if (TOOL_DISPLAY_NAMES[toolName]) {
    return TOOL_DISPLAY_NAMES[toolName];
  }

  // Then try substring match (handles simple names like 'Read' matching 'Reading')
  const lowerToolName = toolName.toLowerCase();
  for (const [key, displayName] of Object.entries(TOOL_DISPLAY_NAMES)) {
    // Skip MCP-prefixed entries for substring matching to avoid false positives
    if (key.startsWith('mcp__')) continue;
    if (lowerToolName.includes(key.toLowerCase())) {
      return displayName;
    }
  }

  // Final fallback: clean up the raw name for display
  // Handle mcp__brain__ prefix by extracting the action
  if (lowerToolName.startsWith('mcp__brain__')) {
    const action = toolName.replace(/^mcp__brain__/, '').replace(/_/g, ' ');
    // Capitalize first letter
    return action.charAt(0).toUpperCase() + action.slice(1);
  }

  return 'Running tool';
};


export const Chat: React.FC<{ isMobile?: boolean }> = ({ isMobile = false }) => {
  const [input, setInput] = useState('');
  const [view, setView] = useState<'chat' | 'history'>('chat');
  const [historyList, setHistoryList] = useState<any[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [attachments, setAttachments] = useState<{ name: string, path: string }[]>([]);
  const [showSearch, setShowSearch] = useState(false);
  const { showToast } = useToast();

  // Command palette state
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [commandSelectedIndex, setCommandSelectedIndex] = useState(0);
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    destructive?: boolean;
    onConfirm: () => void;
  } | null>(null);

  // Ref to hold loadChat function (avoids circular dependency)
  const loadChatRef = useRef<((id: string) => Promise<void>) | null>(null);

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

  // Callback for new message notifications
  const handleNewMessageNotification = useCallback((data: NotificationData) => {
    // Start tab flashing if window not focused
    if (!document.hasFocus()) {
      setShouldFlashTab(true);
    }

    // Show toast notification
    showToast({
      type: 'notification',
      title: data.critical ? 'URGENT: Claude needs your attention' : 'Claude sent you a message',
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
  }, [showToast]);

  // Stop tab flashing when window gains focus
  useEffect(() => {
    const handleFocus = () => setShouldFlashTab(false);
    window.addEventListener('focus', handleFocus);
    return () => window.removeEventListener('focus', handleFocus);
  }, []);

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
    toolName,
    startNewChat,
    loadChat,
    connectionStatus,
    tokenUsage
  } = useClaude({
    onScheduledTaskComplete: handleScheduledTaskComplete,
    onChatTitleUpdate: handleChatTitleUpdate,
    onNewMessageNotification: handleNewMessageNotification,
    onFormRequest: handleFormRequest
  });

  // Keep ref updated
  loadChatRef.current = loadChat;

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

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      const maxHeight = window.innerHeight * 0.5;
      textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px';
    }
  }, [input]);

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, status]);

  // Load history
  useEffect(() => {
    if (view === 'history') {
      fetch(`${API_URL}/chat/history`)
        .then(res => res.json())
        .then(data => setHistoryList(data.chats))
        .catch(console.error);
    }
  }, [view]);

  const handleLoad = (id: string) => {
    loadChat(id);
    setView('chat');
  };

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

  // Command execution context
  const commandContext: CommandContext = {
    startNewChat,
    sendMessage: (msg: string) => {
      if (sendMessage(msg)) {
        setInput('');
        setAttachments([]);
      }
    },
    input,
    setInput,
    showHistory: () => setView('history'),
  };

  // Handle command execution result
  const handleCommandResult = useCallback((result: CommandExecuteResult) => {
    if (result.requiresConfirmation) {
      setConfirmModal({
        isOpen: true,
        title: result.requiresConfirmation.title,
        message: result.requiresConfirmation.message,
        confirmLabel: result.requiresConfirmation.confirmLabel,
        destructive: result.requiresConfirmation.destructive,
        onConfirm: () => {
          result.requiresConfirmation!.onConfirm();
          setConfirmModal(null);
          setInput('');
        },
      });
      return;
    }

    if (result.sendMessage) {
      if (sendMessage(result.sendMessage)) {
        setInput('');
        setAttachments([]);
      }
    } else if (result.replaceInput !== undefined) {
      setInput(result.replaceInput);
    } else if (result.handled) {
      setInput('');
    }
  }, [sendMessage]);

  // Handle command selection from palette
  const handleCommandSelect = useCallback((command: Command) => {
    setShowCommandPalette(false);
    setCommandSelectedIndex(0);

    // If command has args and no args provided, insert command with space
    if (command.hasArgs) {
      const currentArgs = input.replace(/^\/\S*\s*/, '');
      if (!currentArgs.trim()) {
        setInput(`/${command.name} `);
        return;
      }
    }

    // Execute the command
    const args = input.replace(/^\/\S*\s*/, '');
    const result = command.execute(args, commandContext);
    handleCommandResult(result);
  }, [input, commandContext, handleCommandResult]);

  // Handle input changes for command detection
  const handleInputChange = useCallback((value: string) => {
    setInput(value);

    // Show/hide command palette
    if (isCommandInput(value)) {
      const query = getCommandQuery(value);
      // Show palette only when typing the command name (before space)
      if (query !== '' || value === '/') {
        setShowCommandPalette(true);
        setCommandSelectedIndex(0);
      } else {
        setShowCommandPalette(false);
      }
    } else {
      setShowCommandPalette(false);
    }
  }, []);

  // Modified handleSend to process commands
  const handleSendOrCommand = useCallback(() => {
    if (!input.trim() && attachments.length === 0) return;

    // Check if this is a command
    if (isCommandInput(input)) {
      const { command, args } = parseCommand(input);
      if (command) {
        setShowCommandPalette(false);
        const result = command.execute(args, commandContext);
        handleCommandResult(result);
        return;
      }
      // If no matching command, send as regular message (let Claude handle it)
    }

    // Regular message
    let fullMessage = input;
    if (attachments.length > 0) {
      const contextStr = attachments.map(a => `[CONTEXT: ${a.path}]`).join('\n');
      fullMessage = `${contextStr}\n${input}`;
    }

    if (sendMessage(fullMessage)) {
      setInput('');
      setAttachments([]);
      setShowCommandPalette(false);
    }
  }, [input, attachments, sendMessage, commandContext, handleCommandResult]);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
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
    // IMPORTANT: For tool_use status, ALWAYS use the display name mapping
    // The server sends raw tool names in statusText (e.g., "Running mcp__brain__bash...")
    // but we want friendly names (e.g., "Running command...")
    if (status === 'tool_use' && toolName) {
      return `${getToolDisplayName(toolName)}...`;
    }
    // For non-tool states, use statusText if available
    if (statusText) return statusText;
    if (status === 'thinking') {
      return THINKING_PHRASES[Math.floor(Date.now() / 2000) % THINKING_PHRASES.length];
    }
    if (status === 'processing') return 'Processing...';
    return '';
  };

  // Filter out system messages and hidden messages (e.g., ping mode wake-up triggers)
  const visibleMessages = messages.filter(m => m.role !== 'system' && !m.hidden);

  // DEBUG: Log messages received by Chat component
  useEffect(() => {
    console.log('[DEBUG Chat] Messages received:', messages.length);
    console.log('[DEBUG Chat] Visible messages:', visibleMessages.length);
    if (visibleMessages.length > 0) {
      const last = visibleMessages[visibleMessages.length - 1];
      console.log('[DEBUG Chat] Last visible message:', last.role, last.content?.slice(0, 50));
    }
  }, [messages, visibleMessages]);

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

  // Context usage meter (percentage toward auto-compaction)
  const ContextMeter = () => {
    const percent = tokenUsage?.contextPercent ?? 0;
    // Use actualContext if available, otherwise fall back to input tokens
    const tokens = tokenUsage?.actualContext ?? tokenUsage?.input ?? 0;

    // Color based on usage level
    const getColor = () => {
      if (percent >= 90) return 'bg-red-500';
      if (percent >= 75) return 'bg-amber-500';
      if (percent >= 50) return 'bg-yellow-500';
      return 'bg-emerald-500';
    };

    const getTextColor = () => {
      if (percent >= 90) return 'text-red-600 dark:text-red-400';
      if (percent >= 75) return 'text-amber-600 dark:text-amber-400';
      if (percent >= 50) return 'text-yellow-600 dark:text-yellow-400';
      return 'text-[var(--text-muted)]';
    };

    // Show bar if we have any token info (including base overhead)
    if (!tokens && !tokenUsage) return null;

    return (
      <div className="flex items-center gap-2" title={`${tokens.toLocaleString()} tokens used (${percent.toFixed(1)}% until auto-compaction)`}>
        <div className="w-16 h-1.5 bg-[var(--border-color)] rounded-full overflow-hidden">
          <div
            className={clsx("h-full rounded-full transition-all", getColor())}
            style={{ width: `${Math.min(percent, 100)}%` }}
          />
        </div>
        <span className={clsx("text-xs font-medium tabular-nums", getTextColor())}>
          {percent.toFixed(0)}%
        </span>
      </div>
    );
  };

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
              handleLoad(chatId);
              setShowSearch(false);
              // Note: _messageId available for future scroll-to-message feature
            }}
            onClose={() => setShowSearch(false)}
          />
        )}

        {/* History List */}
        <div className="flex-1 overflow-y-auto p-4">
          {historyList.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-[var(--text-muted)]">
              <MessageCircle size={48} strokeWidth={1.5} className="mb-3 opacity-50" />
              <p className="text-sm">No conversations yet</p>
            </div>
          ) : (
            <div className="space-y-2 max-w-2xl mx-auto">
              {historyList.map(chat => (
                <div
                  key={chat.id}
                  onClick={() => handleLoad(chat.id)}
                  className="p-4 bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-color)] hover:border-[var(--accent-primary)] hover:shadow-warm-lg cursor-pointer transition-all group"
                >
                  <div className="flex justify-between items-start">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="font-medium text-[var(--text-primary)] truncate group-hover:text-[var(--accent-primary)] transition-colors">
                          {chat.title}
                        </div>
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
          <div className="flex items-center gap-2">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              <Sparkles size={18} className="text-white" />
            </div>
            <span className="font-semibold text-[var(--text-primary)] tracking-tight">Claude</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <ContextMeter />
          <ConnectionIndicator />
          <button
            onClick={startNewChat}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]"
          >
            <Plus size={16} />
            <span className="hidden sm:inline">New</span>
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
          {visibleMessages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-[60vh] text-[var(--text-muted)]">
              <div
                className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4"
                style={{ backgroundColor: 'var(--accent-light)' }}
              >
                <Sparkles size={32} style={{ color: 'var(--accent-primary)' }} />
              </div>
              <p className="text-lg font-medium text-[var(--text-secondary)] mb-1">How can I help?</p>
              <p className="text-sm text-[var(--text-muted)]">Start a conversation or ask me anything</p>
            </div>
          )}

          {visibleMessages.map((msg, idx) => {
            const isUser = msg.role === 'user';
            const isLastAssistant = msg.role === 'assistant' && idx === visibleMessages.length - 1;

            // Skip form submission messages - the InlineForm already shows the summary
            if (isUser && msg.content.startsWith('[FORM_SUBMISSION:')) {
              return null;
            }

            // Check if this is a continuation (same role as previous, assistant only)
            const prevMsg = idx > 0 ? visibleMessages[idx - 1] : null;
            const isContinuation = !isUser && prevMsg?.role === 'assistant';

            return (
              <div key={msg.id} className={clsx("flex flex-col w-full", isContinuation && 'mt-2')}>
                {/* Message container - user messages align right, assistant full width */}
                <div className={clsx("flex flex-col", isUser ? "items-end" : "items-start w-full")}>
                  {/* Role label - hide for continuation messages */}
                  {!isContinuation && (
                    <div className={clsx(
                      "flex items-center gap-2 mb-2 group",
                      isUser ? "flex-row-reverse" : "flex-row"
                    )}>
                      <span className="text-xs font-medium text-[var(--text-muted)]">
                        {isUser ? 'You' : 'Claude'}
                      </span>

                      {/* Action buttons - show on hover when idle */}
                      {status === 'idle' && isUser && (
                        <button
                          onClick={() => startEdit(msg)}
                          className="p-1 hover:bg-[var(--bg-tertiary)] rounded text-[var(--text-muted)] hover:text-[var(--text-secondary)] opacity-0 group-hover:opacity-100 transition-opacity"
                          title="Edit"
                        >
                          <Pencil size={12} />
                        </button>
                      )}
                    </div>
                  )}

                  {/* Message content */}
                  {msg.formData ? (
                    /* Inline form message */
                    <InlineForm
                      formData={msg.formData}
                      onSubmit={handleFormSubmit}
                    />
                  ) : editingId === msg.id ? (
                    <div className="w-full max-w-[90%] bg-[var(--bg-secondary)] rounded-2xl border border-[var(--border-color)] p-4 shadow-warm">
                      <textarea
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                        className="w-full p-3 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20 outline-none resize-none text-sm"
                        rows={4}
                        autoFocus
                      />
                      <div className="flex justify-end gap-2 mt-3">
                        <button
                          onClick={cancelEdit}
                          className="px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] rounded-lg transition-colors"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={saveEdit}
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
                        <div className="whitespace-pre-wrap font-chat" style={{ fontFamily: 'var(--font-chat)', fontSize: 'var(--font-size-base)' }}>
                          {msg.content.split('\n').filter(line => !line.startsWith('[CONTEXT:')).join('\n').trim()}
                        </div>
                      ) : (
                        <div className="prose prose-sm max-w-none chat-markdown font-chat" style={{ fontFamily: 'var(--font-chat)' }}>
                          <MDEditor.Markdown
                            source={msg.content}
                            style={{
                              backgroundColor: 'transparent',
                              color: 'inherit',
                              fontFamily: 'var(--font-chat)',
                              fontSize: 'var(--font-size-base)',
                              lineHeight: '1.7'
                            }}
                          />
                        </div>
                      )}
                      </div>
                      {/* Message status indicator for user messages */}
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
                      </div>
                    )}
                  </div>
                )}

                  {/* Regenerate button - only on last assistant message */}
                  {isLastAssistant && status === 'idle' && !msg.isStreaming && (
                    <button
                      onClick={() => handleRegenerate(msg.id)}
                      className="mt-2 p-1.5 hover:bg-[var(--bg-tertiary)] rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors flex items-center gap-1 text-xs"
                      title="Regenerate"
                    >
                      <RotateCcw size={12} />
                      <span>Regenerate</span>
                    </button>
                  )}
                </div>
              </div>
            );
          })}

          {/* Status indicator */}
          {status !== 'idle' && (
            <div className="flex items-start gap-3 animate-in">
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
      </div>

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

          {/* Input box with command palette */}
          <div className="relative">
            {/* Command Palette */}
            {showCommandPalette && (
              <CommandPalette
                query={getCommandQuery(input)}
                onSelect={handleCommandSelect}
                onClose={() => {
                  setShowCommandPalette(false);
                  setCommandSelectedIndex(0);
                }}
                selectedIndex={commandSelectedIndex}
                onSelectionChange={setCommandSelectedIndex}
              />
            )}

            <div
              className={clsx(
                "flex items-end gap-3 bg-[var(--bg-tertiary)] border rounded-2xl p-3 input-focus",
                attachments.length > 0 ? "border-[var(--accent-primary)]" : "border-[var(--border-color)]",
                showCommandPalette && "border-[var(--accent-primary)]/50"
              )}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
            >
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => handleInputChange(e.target.value)}
                placeholder="Message Claude... (type / for commands)"
                rows={1}
                onKeyDown={(e) => {
                  // Let command palette handle navigation keys when open
                  if (showCommandPalette && ['ArrowUp', 'ArrowDown', 'Tab', 'Escape'].includes(e.key)) {
                    return; // CommandPalette handles these via window listener
                  }
                  if (e.key === 'Enter') {
                    if (showCommandPalette && !e.shiftKey) {
                      // Enter in palette selects command - handled by CommandPalette
                      return;
                    }
                    if (isMobile) {
                      // Mobile: Enter adds newline
                      return;
                    } else if (!e.shiftKey) {
                      e.preventDefault();
                      handleSendOrCommand();
                    }
                  }
                }}
                className="flex-1 bg-transparent border-none text-[15px] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:ring-0 focus:outline-none resize-none py-1.5 px-1"
                style={{ minHeight: '24px', maxHeight: '150px' }}
              />

              {status !== 'idle' ? (
                <button
                  onClick={() => stopGeneration()}
                  className="p-2.5 rounded-xl transition-all flex-shrink-0 bg-red-500 hover:bg-red-600 text-white shadow-md"
                  title="Stop generating"
                >
                  <Square size={18} fill="currentColor" />
                </button>
              ) : (
                <button
                  onClick={handleSendOrCommand}
                  disabled={!input.trim() && attachments.length === 0}
                  className={clsx(
                    "p-2.5 rounded-xl transition-all flex-shrink-0 btn-primary",
                    (!input.trim() && attachments.length === 0)
                      ? "bg-[var(--bg-tertiary)] text-[var(--text-muted)] cursor-not-allowed border border-[var(--border-color)]"
                      : "text-white shadow-md"
                  )}
                  style={{
                    backgroundColor: (input.trim() || attachments.length > 0)
                      ? 'var(--accent-primary)'
                      : undefined
                  }}
                >
                  <Send size={18} />
                </button>
              )}
            </div>
          </div>

          <p className="text-xs text-[var(--text-muted)] text-center mt-2">
            Press Enter to send, Shift+Enter for new line · Type / for commands
          </p>
        </div>
      </div>

      {/* Confirm Modal */}
      {confirmModal && (
        <ConfirmModal
          isOpen={confirmModal.isOpen}
          title={confirmModal.title}
          message={confirmModal.message}
          confirmLabel={confirmModal.confirmLabel}
          destructive={confirmModal.destructive}
          onConfirm={confirmModal.onConfirm}
          onCancel={() => setConfirmModal(null)}
        />
      )}
    </div>
  );
};
