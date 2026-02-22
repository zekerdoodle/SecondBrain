import { useState, useRef, useCallback, useEffect } from 'react';
import type { ChatMessage, ChatImageRef, MessageStatus } from './types';
import { WS_URL, API_URL } from './config';
import { useVisibility, type VisibilityState } from './hooks/useVisibility';
import { extractToolSummary } from './utils/toolDisplay';

const SESSION_KEY = 'second_brain_session_id';
const PENDING_MSG_KEY = 'second_brain_pending_message';
const LAST_SYNC_KEY = 'second_brain_last_sync';

// Default base overhead (fetched dynamically on load)

// Message queue entry for reliability
interface QueuedMessage {
  id: string;
  content: string;
  sessionId: string;
  timestamp: number;
  acknowledged: boolean;
  status: MessageStatus;
  serverTimestamp?: number;
}

// User message being injected mid-stream (while Claude is responding)
export type QueuedMessageStatus = 'pending' | 'confirmed' | 'not_delivered';

export interface UserQueuedMessage {
  id: string;
  content: string;
  timestamp: number;
  status: QueuedMessageStatus;
}

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';
export type ChatStatus = 'idle' | 'thinking' | 'tool_use' | 'processing';

export interface ActiveTool {
  id: string;
  name: string;
  args?: string;
  summary?: string;
  startedAt: number;
}

// extractToolSummary is now imported from ./utils/toolDisplay

export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm?: string;
}

export interface ClaudeHook {
  messages: ChatMessage[];
  sendMessage: (text: string, images?: ChatImageRef[]) => boolean;
  editMessage: (messageId: string, newContent: string) => boolean;
  updateFormMessage: (formId: string, submittedValues: Record<string, any>) => void;
  regenerateMessage: (messageId: string) => boolean;
  stopGeneration: () => boolean;
  deleteChat: (sessionId: string) => Promise<boolean>;
  status: ChatStatus;
  statusText: string;
  toolName: string | null;
  activeTools: Map<string, ActiveTool>;
  startNewChat: () => void;
  loadChat: (id: string, agentHint?: string | null) => Promise<void>;
  sessionId: string;
  connectionStatus: ConnectionStatus;
  // Message queue for sending while Claude is responding
  queuedMessages: UserQueuedMessage[];
  clearQueuedMessages: () => void;
  dismissQueuedMessage: (id: string) => void;
  // Multi-agent support
  currentAgent: string | null;
  sendMessageWithAgent: (text: string, agent?: string, images?: ChatImageRef[]) => boolean;
  // Todo list from agents using TodoWrite
  todos: TodoItem[];
}

export interface NotificationData {
  chatId: string;
  preview: string;
  critical: boolean;
  playSound: boolean;
}

export interface FormRequestData {
  formId: string;
  title: string;
  description?: string;
  fields: Array<{
    id: string;
    type: 'text' | 'textarea' | 'select' | 'checkbox' | 'number' | 'date';
    label: string;
    required?: boolean;
    placeholder?: string;
    options?: Array<{ label: string; value: string }>;
    defaultValue?: any;
  }>;
  prefill?: Record<string, any>;
  version?: number;
}

export interface ChessGameState {
  id: string;
  fen: string;
  claude_color: 'white' | 'black';
  user_color: 'white' | 'black';
  moves: Array<{
    san: string;
    uci: string;
    player: 'claude' | 'user';
    timestamp: string;
  }>;
  game_over: boolean;
  result: string | null;
  captured: {
    white: string[];
    black: string[];
  };
}

export interface ChatCreatedData {
  chat: { id: string; title: string; updated: number; is_system: boolean; scheduled: boolean; agent?: string };
}

export interface ClaudeOptions {
  onScheduledTaskComplete?: (data: { session_id: string; title: string }) => void;
  onChatTitleUpdate?: (data: { session_id: string; title: string; confidence: number }) => void;
  onChatCreated?: (data: ChatCreatedData) => void;
  onNewMessageNotification?: (data: NotificationData) => void;
  onFormRequest?: (data: FormRequestData) => void;
  onChessUpdate?: (game: ChessGameState) => void;
  // Multi-instance support (split view)
  instanceId?: string;           // Namespaces localStorage keys. Default: 'primary'
  enabled?: boolean;             // Controls WebSocket connection. Default: true
  suppressGlobalEvents?: boolean; // Skips global event callbacks (notifications, chat_created, etc). Default: false
}

export const useClaude = (options: ClaudeOptions = {}): ClaudeHook => {
  const { instanceId = 'primary', enabled = true, suppressGlobalEvents = false } = options;

  // Namespace localStorage/sessionStorage keys for multi-instance support
  const keySuffix = instanceId === 'primary' ? '' : `_${instanceId}`;
  const sessionKey = SESSION_KEY + keySuffix;
  const pendingMsgKey = PENDING_MSG_KEY + keySuffix;
  const lastSyncKey = LAST_SYNC_KEY + keySuffix;

  // Messages come from server only - no localStorage
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string>(() => {
    return localStorage.getItem(sessionKey) || 'new';
  });

  const [status, setStatus] = useState<ChatStatus>('idle');
  const [statusText, setStatusText] = useState<string>('');
  const [activeTools, setActiveTools] = useState<Map<string, ActiveTool>>(new Map());
  const [currentAgent, setCurrentAgent] = useState<string | null>(null);
  const [todos, setTodos] = useState<TodoItem[]>([]);
  // Derived: first active tool name for backward compat
  const toolName = activeTools.size > 0 ? Array.from(activeTools.values())[0].name : null;
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');

  const ws = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);

  // Refs for multi-instance support (needed in stale closures)
  const enabledRef = useRef(enabled);
  useEffect(() => { enabledRef.current = enabled; }, [enabled]);
  const suppressGlobalEventsRef = useRef(suppressGlobalEvents);
  useEffect(() => { suppressGlobalEventsRef.current = suppressGlobalEvents; }, [suppressGlobalEvents]);

  // Visibility tracking for notifications
  const visibilityHeartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Ref for sessionId - needed for stale-closure-safe filtering in onmessage handler
  const sessionIdRef = useRef(sessionId);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);

  // Block-based delta batching: accumulate deltas per block and flush once per frame
  const pendingDeltas = useRef(new Map<string, string>());
  const rafId = useRef<number | null>(null);

  // Guard flag: when true, streaming events are ignored until the 'state' response arrives.
  // This prevents the race condition where stale/early events corrupt state during chat switching.
  const awaitingStateResponse = useRef(false);

  const flushDeltas = useCallback(() => {
    rafId.current = null;
    const deltas = pendingDeltas.current;
    if (deltas.size === 0) return;

    setMessages(prev => {
      let updated = prev;
      for (const [key, delta] of deltas) {
        const [msgId, blockId] = key.split(':');
        updated = updated.map(msg => {
          if (msg.id !== msgId || !msg.blocks) return msg;
          return {
            ...msg,
            blocks: msg.blocks.map(block =>
              block.id === blockId
                ? { ...block, content: block.content + delta }
                : block
            )
          };
        });
      }
      return updated;
    });

    pendingDeltas.current = new Map();
  }, []);

  const scheduleFlush = useCallback(() => {
    if (rafId.current === null) {
      rafId.current = requestAnimationFrame(flushDeltas);
    }
  }, [flushDeltas]);

  // Message queue for reliability - track pending messages awaiting ACK
  const pendingMessages = useRef<Map<string, QueuedMessage>>(new Map());
  const ackTimeoutMs = 5000; // 5 seconds to receive ACK before showing warning

  // User message queue - messages sent while Claude is responding
  // These will be sent automatically when the current turn completes
  const [queuedMessages, setQueuedMessages] = useState<UserQueuedMessage[]>([]);
  // Flag to trigger queue processing after turn completion
  const shouldProcessQueue = useRef(false);
  // Store injected messages confirmed by server, with their injection position
  interface InjectedMessageInfo {
    message: ChatMessage;
    injectionBlockIndex: number; // How many blocks the streaming assistant message had at injection time
    injectionMessageId: string;  // ID of the assistant message being streamed
  }
  const injectedMessagesRef = useRef<InjectedMessageInfo[]>([]);

  // Only persist sessionId
  useEffect(() => {
    localStorage.setItem(sessionKey, sessionId);
  }, [sessionId, sessionKey]);


  // Recover pending message on mount (if component unmounted during send)
  // This is a BACKUP mechanism - the primary sync happens in WebSocket onopen
  // This handles edge cases where WebSocket connection fails or takes too long
  useEffect(() => {
    const recoverPendingMessage = async () => {
      try {
        const pendingStr = sessionStorage.getItem(pendingMsgKey);
        if (!pendingStr) return;

        const pending = JSON.parse(pendingStr);
        // Only recover if it's recent (within last 5 minutes) and for this session
        const isRecent = Date.now() - pending.timestamp < 300000; // 5 minutes
        const isSameSession = pending.sessionId === sessionId || sessionId === 'new';

        if (!isRecent || !isSameSession) {
          console.log('Pending message too old or wrong session, clearing');
          sessionStorage.removeItem(pendingMsgKey);
          return;
        }

        console.log('Found pending message, checking with server:', pending.content.slice(0, 50));

        // Check with server if message was already processed
        // The WebSocket onopen handler will load the full chat history,
        // so we just need to check if our message is there
        const targetSession = pending.sessionId !== 'new' ? pending.sessionId : sessionId;
        if (targetSession !== 'new') {
          try {
            // Check if the message exists in the saved chat
            const chatRes = await fetch(`${API_URL}/chat/history/${targetSession}`);
            if (chatRes.ok) {
              const chatData = await chatRes.json();
              if (chatData.messages?.some((m: ChatMessage) => m.id === pending.id)) {
                // Message was already saved - WebSocket onopen will load it
                console.log('Pending message already saved on server, clearing sessionStorage');
                sessionStorage.removeItem(pendingMsgKey);
                return;
              }
            }

            // Check if server is actively processing this message
            const pendingRes = await fetch(`${API_URL}/chat/pending/${targetSession}`);
            if (pendingRes.ok) {
              const serverPending = await pendingRes.json();

              if (serverPending.has_pending && serverPending.msg_id === pending.id) {
                // Server knows about our message - wait for WebSocket events
                console.log(`Server tracking our message: status=${serverPending.status}`);
                // Don't clear sessionStorage yet - let the done event clear it
                return;
              }
            }
          } catch (e) {
            console.warn('Could not check server for pending message:', e);
          }
        }

        // If we get here, the message wasn't found on server
        // This means the send likely failed - we should NOT auto-add it
        // because WebSocket onopen will handle the proper state
        console.log('Pending message not found on server, clearing (likely failed send)');
        sessionStorage.removeItem(pendingMsgKey);
      } catch (e) {
        console.warn('Error recovering pending message:', e);
        sessionStorage.removeItem(pendingMsgKey);
      }
    };

    // Small delay to let WebSocket connect first
    const timeoutId = setTimeout(recoverPendingMessage, 500);
    return () => clearTimeout(timeoutId);
  }, []); // Only on mount

  // Track if this is a user-initiated session change (vs automatic)
  const isUserInitiatedLoad = useRef(false);

  // Load chat from server when sessionId changes
  // NOTE: The WebSocket subscribe handler is the PRIMARY source of truth for chat state.
  // It returns full state (including in-memory streaming messages) via the 'state' response.
  // This effect is a FALLBACK for when WebSocket is not available (e.g., during initial connect).
  // BUG FIX: Previously, this REST fetch would race with the WebSocket 'state' response and
  // overwrite in-memory streaming messages with stale on-disk data, causing content to disappear
  // when switching back to an actively-streaming chat.
  useEffect(() => {
    if (sessionId && sessionId !== 'new') {
      if (isUserInitiatedLoad.current) {
        // The WebSocket subscribe (sent in loadChat) will handle state loading.
        // Only fetch via REST as a fallback if WebSocket didn't respond in time.
        const fallbackTimeoutId = setTimeout(() => {
          // If the state response already arrived, skip the REST fetch
          if (!awaitingStateResponse.current) {
            isUserInitiatedLoad.current = false;
            return;
          }
          // WebSocket state hasn't arrived — fallback to REST
          console.log('[FALLBACK] WS state not received in time, fetching via REST');
          fetch(`${API_URL}/chat/history/${sessionId}`)
            .then(res => {
              if (res.ok) return res.json();
              throw new Error("Chat not found");
            })
            .then(data => {
              // Only apply if we're STILL waiting (state could have arrived in the meantime)
              const msgs = data.display_messages || data.messages;
              if (awaitingStateResponse.current && msgs && Array.isArray(msgs)) {
                awaitingStateResponse.current = false;
                setMessages(msgs);
              }
              isUserInitiatedLoad.current = false;
            })
            .catch(err => {
              console.warn("Could not load chat:", err);
              if (awaitingStateResponse.current) {
                awaitingStateResponse.current = false;
                setMessages([]);
              }
              isUserInitiatedLoad.current = false;
            });
        }, 2000); // 2 second timeout before falling back to REST
        return () => clearTimeout(fallbackTimeoutId);
      }
    } else if (isUserInitiatedLoad.current) {
      // Only clear messages if user explicitly started a new chat
      setMessages([]);
      isUserInitiatedLoad.current = false;
    }
  }, [sessionId]);

  // Send visibility update to server
  const sendVisibilityUpdate = useCallback((isActive: boolean, chatId?: string) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      const effectiveChatId = chatId || (sessionId !== 'new' ? sessionId : undefined);
      ws.current.send(JSON.stringify({
        action: 'visibility_update',
        isActive,
        chatId: effectiveChatId
      }));
    }
  }, [sessionId]);

  const connect = useCallback(() => {
    // Skip connection when instance is disabled (e.g., split view not yet open)
    if (!enabledRef.current) return;
    if (ws.current?.readyState === WebSocket.OPEN) return;

    // CRITICAL FIX: Close the old socket and remove its handlers before creating a new one
    // This prevents duplicate event processing when reconnecting
    if (ws.current) {
      // Remove handlers to prevent them from firing during close
      ws.current.onmessage = null;
      ws.current.onclose = null;
      ws.current.onerror = null;
      // Close if not already closed
      if (ws.current.readyState !== WebSocket.CLOSED) {
        ws.current.close();
      }
    }

    setConnectionStatus('connecting');
    const socket = new WebSocket(`${WS_URL}/ws/chat`);

    socket.onopen = () => {
      setConnectionStatus('connected');
      reconnectAttempts.current = 0;

      // SIMPLE ARCHITECTURE: Just subscribe and let server send us the state.
      // No complex sync logic, no race conditions, no multiple sources of truth.
      // Server is the ONLY source of truth.
      const currentSessionId = localStorage.getItem(sessionKey) || sessionId;

      // Send subscribe - server will respond with full state
      socket.send(JSON.stringify({
        action: 'subscribe',
        sessionId: currentSessionId
      }));
    };

    socket.onmessage = (event) => {
      // CRITICAL FIX: Ignore messages if this socket is no longer the active one
      // This prevents duplicate event processing during reconnect
      if (ws.current !== socket) {
        console.log('[WS] Ignoring message from stale socket');
        return;
      }

      let data;
      try {
        data = JSON.parse(event.data);
      } catch (e) {
        console.error('[WS] Failed to parse message:', e);
        return;
      }

      // Multi-chat concurrent streaming: filter out events for other sessions.
      // Server accumulates state per-session; we'll get it via subscribe when switching back.
      // IMPORTANT: message_accepted and session_init must be included here to prevent
      // stale events from corrupting state during chat switches (they set sessionId/messages).
      const streamingEventTypes = new Set([
        'message_start', 'block_start', 'block_delta', 'block_end', 'block_update',
        'message_end', 'session_status',
        'content_delta', 'tool_start', 'tool_end',
        'status', 'done', 'todo_update',
        'message_accepted', 'session_init', 'message_received'
      ]);
      if (streamingEventTypes.has(data.type) && data.sessionId &&
          sessionIdRef.current !== 'new' && data.sessionId !== sessionIdRef.current) {
        return;
      }

      // Guard: after a chat switch, ignore ALL streaming events until the 'state' response
      // arrives with the full snapshot. Without this, stale/early events can corrupt state.
      // The 'state' event itself is allowed through (it clears the flag).
      if (awaitingStateResponse.current && data.type !== 'state') {
        // Silently drop streaming events while awaiting state snapshot
        return;
      }

      switch (data.type) {
        case 'state':
          // SERVER IS THE SOURCE OF TRUTH - just render what it sends
          // Clear the guard flag — state has arrived, streaming events can now be processed
          awaitingStateResponse.current = false;
          try {
            if (data.sessionId && data.sessionId !== 'new') {
              setSessionId(data.sessionId);
            }
            if (data.agent !== undefined) {
              setCurrentAgent(data.agent || null);
            }

            // During streaming: filter out injected messages from state data BEFORE
            // setting messages. The server appends injected messages at the end of its
            // streaming state, but the client needs the done/interrupted handler to place
            // them at the correct split position using injectedMessagesRef.
            if (data.isProcessing && data.messages && injectedMessagesRef.current.length > 0) {
              const injectedIds = new Set(injectedMessagesRef.current.map(info => info.message.id));
              data.messages = data.messages.filter((m: ChatMessage) => !injectedIds.has(m.id));
            }
            // Also filter any server-flagged injected messages we don't have position info for
            if (data.isProcessing && data.messages) {
              data.messages = data.messages.filter((m: ChatMessage) => !m.injected);
            }

            // Dedup by ID — safety net against duplicate messages from any source
            if (data.messages) {
              const seen = new Set<string>();
              data.messages = data.messages.filter((m: ChatMessage) => {
                if (seen.has(m.id)) return false;
                seen.add(m.id);
                return true;
              });
              setMessages(data.messages);
            }
            // Clear queued messages on reconnect/state sync (only when NOT streaming)
            if (!data.isProcessing) {
              setQueuedMessages([]);
              injectedMessagesRef.current = [];
            }
            // During streaming: keep injectedMessagesRef and queuedMessages intact
            // for the done/interrupted handlers to use

            // Set status
            if (data.isProcessing) {
              setStatus(data.status === 'idle' ? 'thinking' : (data.status || 'thinking'));
              setStatusText(data.statusText || 'Processing...');
              setActiveTools(new Map()); // Will be populated by subsequent block_start events
              setTodos(Array.isArray(data.todos) ? data.todos : []);

              // Restore active tools from server state (legacy format)
              if (data.activeTools && Object.keys(data.activeTools).length > 0) {
                const restored = new Map<string, ActiveTool>();
                for (const [tid, info] of Object.entries(data.activeTools)) {
                  const toolInfo = info as { name: string; args?: string };
                  restored.set(tid, {
                    id: tid,
                    name: toolInfo.name,
                    args: toolInfo.args,
                    summary: toolInfo.args ? extractToolSummary(toolInfo.name, toolInfo.args) : undefined,
                    startedAt: Date.now()
                  });
                }
                setActiveTools(restored);
              }

              // Reconstruct activeTools from blocks in streaming messages
              if (data.messages) {
                const tools = new Map<string, ActiveTool>();
                for (const msg of data.messages) {
                  if (msg.blocks) {
                    for (const block of msg.blocks) {
                      if (block.type === 'tool_use' && block.status === 'in_progress') {
                        tools.set(block.id, {
                          id: block.id,
                          name: block.tool_name || 'tool',
                          startedAt: Date.now(),
                        });
                      }
                    }
                  }
                }
                if (tools.size > 0) {
                  setActiveTools(tools);
                  setStatus('tool_use');
                  const firstName = tools.values().next().value?.name;
                  setStatusText(`Running ${firstName}...`);
                }
              }
            } else {
              setStatus('idle');
              setStatusText('');
              setActiveTools(new Map());
              setTodos(Array.isArray(data.todos) ? data.todos : []);
            }

          } catch (err) {
            console.error('[STATE] Error processing state:', err);
          }
          break;

        case 'message_received':
          // Backend acknowledged receipt of our message (legacy - kept for compatibility)
          {
            const msgId = data.msgId;
            const serverTimestamp = data.timestamp;
            if (msgId && pendingMessages.current.has(msgId)) {
              const pending = pendingMessages.current.get(msgId)!;
              pending.acknowledged = true;
              pending.status = 'confirmed';
              pending.serverTimestamp = serverTimestamp;
              setStatusText('Thinking...');
            }
          }
          break;

        case 'message_accepted':
          // BACKEND-AUTHORITATIVE: Server persisted the message and is broadcasting it
          // This is the SOURCE OF TRUTH - we display what server tells us
          {

            // Update session ID if provided
            if (data.sessionId && data.sessionId !== 'new') {
              setSessionId(data.sessionId);
            }

            // Add/update the message from server's authoritative state
            if (data.message) {
              setMessages(prev => {
                // Check if we already have this message (optimistic add)
                const existingIdx = prev.findIndex(m => m.id === data.message.id);
                if (existingIdx >= 0) {
                  // Update existing message with server's confirmed version
                  const updated = [...prev];
                  updated[existingIdx] = { ...data.message, status: 'confirmed' as const };
                  return updated;
                } else {
                  // Add new message from server
                  return [...prev, { ...data.message, status: 'confirmed' as const }];
                }
              });

              // Clear from pending queue
              pendingMessages.current.delete(data.message.id);
            }

            setStatusText('Thinking...');
          }
          break;

        case 'session_init':
          // CRITICAL: Update sessionId immediately so localStorage is correct if user refreshes
          // This is sent BEFORE processing starts, so we need to capture it now
          if (data.id && data.id !== 'new') {
            setSessionId(data.id);
            // Note: setSessionId triggers useEffect that saves to localStorage
          }
          // Capture agent from session_init (default to "ren" for backward compatibility)
          if (data.agent !== undefined) {
            setCurrentAgent(data.agent || "ren");
          }
          break;

        // --- New block-based streaming events ---

        case 'message_start': {
          const { message_id, role } = data;
          if (role === 'assistant') {
            setMessages(prev => [...prev, {
              id: message_id,
              role: 'assistant' as const,
              content: '',
              blocks: [],
              isStreaming: true,
            }]);
          }
          setStatus('thinking');
          break;
        }

        case 'block_start': {
          const { message_id, block } = data;
          setMessages(prev => prev.map(msg => {
            if (msg.id !== message_id || !msg.blocks) return msg;
            return {
              ...msg,
              blocks: [...msg.blocks, block],
            };
          }));

          // Update status based on block type
          if (block.type === 'thinking') {
            setStatus('thinking');
            setStatusText('Thinking...');
          } else if (block.type === 'text') {
            setStatus('thinking');
            setStatusText('');
          } else if (block.type === 'tool_use') {
            setStatus('tool_use');
            setStatusText(`Running ${block.tool_name}...`);
            setActiveTools(prev => {
              const next = new Map(prev);
              next.set(block.id, {
                id: block.id,
                name: block.tool_name || 'tool',
                startedAt: Date.now(),
              });
              return next;
            });
          }
          break;
        }

        case 'block_delta': {
          const { message_id, block_id, delta } = data;
          if (delta) {
            const key = `${message_id}:${block_id}`;
            const existing = pendingDeltas.current.get(key) || '';
            pendingDeltas.current.set(key, existing + delta);
            scheduleFlush();
          }
          break;
        }

        case 'block_end': {
          const { message_id, block_id, metadata } = data;
          // Flush any pending deltas for this block first
          if (rafId.current) {
            cancelAnimationFrame(rafId.current);
            rafId.current = null;
            flushDeltas();
          }

          setMessages(prev => prev.map(msg => {
            if (msg.id !== message_id || !msg.blocks) return msg;
            return {
              ...msg,
              blocks: msg.blocks.map(block => {
                if (block.id !== block_id) return block;
                return {
                  ...block,
                  status: 'complete' as const,
                  ...(metadata?.duration_ms ? { duration_ms: metadata.duration_ms } : {}),
                };
              }),
            };
          }));

          // Remove from activeTools if it was a tool
          setActiveTools(prev => {
            const next = new Map(prev);
            next.delete(block_id);
            if (next.size === 0) {
              setStatus('thinking');
              setStatusText('');
            }
            return next;
          });
          break;
        }

        case 'block_update': {
          const { message_id, block_id, block: updatedBlock } = data;
          setMessages(prev => prev.map(msg => {
            if (msg.id !== message_id || !msg.blocks) return msg;
            return {
              ...msg,
              blocks: msg.blocks.map(b =>
                b.id === block_id ? { ...b, ...updatedBlock } : b
              ),
            };
          }));
          break;
        }

        case 'message_end': {
          const { message_id } = data;
          setMessages(prev => prev.map(msg => {
            if (msg.id !== message_id) return msg;
            return { ...msg, isStreaming: false };
          }));
          break;
        }

        case 'session_status': {
          if (data.status === 'idle') {
            setStatus('idle');
            setStatusText('');
            setActiveTools(new Map());
          }
          break;
        }

        // --- Legacy streaming events (kept for backward compatibility) ---

        case 'content_delta':
          // Legacy: fall through to block_delta-style handling
          // Server should be sending block events instead, but keep this as fallback
          break;

        case 'tool_start':
          // Legacy: handled by block_start now
          break;

        case 'tool_end':
          // Legacy: handled by block_end now
          break;

        case 'status':
          setStatusText(data.text || '');
          lastActivityTime.current = Date.now();
          break;

        case 'truncate':
          // BACKEND-AUTHORITATIVE: Server truncated - just accept the new state
          // This is broadcast to ALL clients, ensuring multi-device consistency
          if (data.messages) {
            setMessages(data.messages);
          }
          // Update session ID if provided (for edit/regenerate)
          if (data.sessionId && data.sessionId !== 'new') {
            setSessionId(data.sessionId);
          }
          // Reset delta batching
          pendingDeltas.current.clear();
          break;

        case 'done':
          // Flush any pending deltas
          if (rafId.current) {
            cancelAnimationFrame(rafId.current);
            rafId.current = null;
            flushDeltas();
          }

          setStatus('idle');
          setStatusText('');
          setActiveTools(new Map());
          setTodos([]);

          // Mark all messages and blocks as complete, and insert any deferred injected messages
          setMessages(prev => {
            console.log(`[DONE] marking ${prev.length} messages complete, roles: ${prev.map(m => m.role).join(',')}`);
            let updated = prev.map(m => ({
              ...m,
              isStreaming: false,
              ...(m.blocks ? {
                blocks: m.blocks.map(b => ({ ...b, status: 'complete' as const })),
              } : {}),
              ...(m.status && m.status !== 'complete' ? { status: 'complete' as const } : {}),
            }));

            // Insert injected messages at their exact injection positions by splitting assistant messages
            const injectedInfos = injectedMessagesRef.current;
            if (injectedInfos.length > 0) {
              console.log(`[DONE] inserting ${injectedInfos.length} injected messages with position info`);
              // Deduplicate: skip any injected messages that are already in the messages array
              // (can happen if a state event arrived after injection and included the message)
              const existingIds = new Set(updated.map(m => m.id));
              const dedupedInfos = injectedInfos.filter(info => !existingIds.has(info.message.id));
              if (dedupedInfos.length < injectedInfos.length) {
                console.log(`[DONE] skipping ${injectedInfos.length - dedupedInfos.length} already-present injected messages`);
              }
              // Process in reverse order so earlier split indices remain valid
              for (let i = dedupedInfos.length - 1; i >= 0; i--) {
                const { message: injMsg, injectionBlockIndex, injectionMessageId } = dedupedInfos[i];
                const targetIdx = updated.findIndex(m => m.id === injectionMessageId);

                if (targetIdx >= 0 && updated[targetIdx].blocks && updated[targetIdx].blocks!.length > 0) {
                  const targetMsg = updated[targetIdx];
                  const blocks = targetMsg.blocks!;
                  console.log(`[DONE] splitting message ${injectionMessageId} at block ${injectionBlockIndex}/${blocks.length}`);

                  if (injectionBlockIndex <= 0) {
                    // Injection before any blocks — insert before the assistant message
                    updated = [
                      ...updated.slice(0, targetIdx),
                      injMsg,
                      ...updated.slice(targetIdx),
                    ];
                  } else if (injectionBlockIndex >= blocks.length) {
                    // Injection after all blocks — insert after the assistant message
                    updated = [
                      ...updated.slice(0, targetIdx + 1),
                      injMsg,
                      ...updated.slice(targetIdx + 1),
                    ];
                  } else {
                    // Split the assistant message at the injection point.
                    // IMPORTANT: Use unique IDs for both halves to prevent ID collisions
                    // when multiple injections target the same message. Clear `content`
                    // since block-based messages render via blocks, not content.
                    const firstHalf: ChatMessage = {
                      id: `${targetMsg.id}-pre-${i}`,
                      role: targetMsg.role,
                      blocks: blocks.slice(0, injectionBlockIndex),
                      isStreaming: false,
                      content: '',
                    };
                    const secondHalf: ChatMessage = {
                      id: `${targetMsg.id}-cont-${i}`,
                      role: targetMsg.role,
                      blocks: blocks.slice(injectionBlockIndex),
                      isStreaming: false,
                      content: '',
                    };
                    updated = [
                      ...updated.slice(0, targetIdx),
                      firstHalf,
                      injMsg,
                      secondHalf,
                      ...updated.slice(targetIdx + 1),
                    ];
                  }
                } else {
                  // Fallback: insert before last assistant message
                  const lastAssistantIdx = updated.findLastIndex(m => m.role === 'assistant');
                  if (lastAssistantIdx > 0) {
                    updated = [
                      ...updated.slice(0, lastAssistantIdx),
                      injMsg,
                      ...updated.slice(lastAssistantIdx),
                    ];
                  } else {
                    updated.push(injMsg);
                  }
                }
              }
              injectedMessagesRef.current = [];
            }

            return updated;
          });

          // Clear pending messages queue - conversation turn complete
          pendingMessages.current.clear();
          pendingDeltas.current.clear();

          // Signal that we should process any queued user messages
          shouldProcessQueue.current = true;

          // Mark remaining queued messages: confirmed ones are cleared (already inserted),
          // pending ones are marked as not_delivered (server didn't process them before turn ended)
          setQueuedMessages(prev => {
            console.log(`[DONE] queuedMessages remaining: ${prev.length}, statuses: ${prev.map(m => m.status).join(',')}`);
            if (prev.length === 0) return prev;
            const remaining = prev.filter(m => m.status !== 'confirmed');
            if (remaining.length === 0) return [];
            return remaining.map(m => m.status !== 'not_delivered' ? { ...m, status: 'not_delivered' as QueuedMessageStatus } : m);
          });

          // Clear pending message - it's now saved on server
          try {
            sessionStorage.removeItem(pendingMsgKey);
            if (data.sessionId) {
              sessionStorage.setItem(lastSyncKey, JSON.stringify({
                sessionId: data.sessionId,
                timestamp: Date.now()
              }));
            }
          } catch (e) {
            // Ignore
          }

          if (data.sessionId) {
            setSessionId(data.sessionId);
          }

          // Keep streaming blocks intact — don't sync with server's disk format.
          // The streaming state captured everything (text, thinking, tools) with proper
          // block-based rendering. Server's conv.messages use different IDs and don't
          // include blocks, so syncing destroys thinking blocks and per-block separation.
          // Server already saved to disk for future reloads; current session keeps blocks.
          break;

        case 'error':
          if (data.text?.includes("permission")) return;
          setMessages(prev => [...prev, {
            id: 'error-' + Date.now(),
            role: 'assistant',
            content: `Error: ${data.text}`,
            isError: true
          }]);
          setStatus('idle');
          setStatusText('');
          pendingDeltas.current.clear();
          break;

        case 'result_meta':
          // Usage data received but not displayed (SDK reports cumulative, not per-turn)
          break;

        case 'scheduled_task_complete':
          if (suppressGlobalEventsRef.current) break;
          console.log(`Scheduled task complete: ${data.title} (session: ${data.session_id})`);
          options.onScheduledTaskComplete?.({ session_id: data.session_id, title: data.title });
          break;

        case 'new_message_notification':
          if (suppressGlobalEventsRef.current) break;
          console.log(`New message notification: ${data.preview?.slice(0, 50)} (chat: ${data.chatId})`);
          options.onNewMessageNotification?.({
            chatId: data.chatId,
            preview: data.preview || '',
            critical: data.critical || false,
            playSound: data.playSound ?? true
          });
          break;

        case 'chat_title_update':
          if (suppressGlobalEventsRef.current) break;
          console.log(`Chat title updated: "${data.title}" (session: ${data.session_id}, confidence: ${data.confidence})`);
          options.onChatTitleUpdate?.({ session_id: data.session_id, title: data.title, confidence: data.confidence });
          break;

        case 'chat_created':
          if (suppressGlobalEventsRef.current) break;
          console.log(`New chat created: ${data.chat?.id} - "${data.chat?.title}"`);
          options.onChatCreated?.(data);
          break;

        case 'form_request':
          // Form tool triggered - add form as inline message
          // This may come from initial broadcast OR from reconnect (pending_form)
          console.log(`Form request received: ${data.title} (formId: ${data.formId})`);
          setMessages(prev => {
            // Check if this form is already in the messages (prevent duplicates on reconnect)
            const existingForm = prev.find(m => m.formData?.formId === data.formId);
            if (existingForm) {
              console.log(`Form ${data.formId} already exists, skipping duplicate`);
              return prev;
            }
            return [...prev, {
              id: `form-${data.formId}-${Date.now()}`,
              role: 'assistant',
              content: '',
              formData: {
                formId: data.formId,
                title: data.title,
                description: data.description,
                fields: data.fields || [],
                prefill: data.prefill,
                status: 'pending' as const
              }
            }];
          });
          // Also call callback if provided (for compatibility)
          options.onFormRequest?.({
            formId: data.formId,
            title: data.title,
            description: data.description,
            fields: data.fields || [],
            prefill: data.prefill,
            version: data.version
          });
          break;

        case 'chess_update':
          // Chess game state update
          console.log('Chess game update received:', data.game?.id);
          options.onChessUpdate?.(data.game);
          break;

        case 'todo_update':
          // TodoWrite tool fired - update todo list display
          if (data.todos && Array.isArray(data.todos)) {
            setTodos(data.todos);
          }
          break;

        case 'server_restarted':
          // Only primary instance handles reload — secondary instances will be cleaned up by the reload
          if (suppressGlobalEventsRef.current) break;
          // Server was restarted - this is a clean slate
          console.log('Server was restarted:', data.message);
          console.log('Previous active sessions:', data.active_sessions);

          // FIX BUG 3: CRITICAL - Reset ALL processing state on server restart
          setStatus('idle');
          setStatusText('');
          setActiveTools(new Map());
          setTodos([]);
          pendingDeltas.current.clear();
          pendingMessages.current.clear();

          // Clear any pending message in sessionStorage too
          // It's stale after server restart
          try {
            sessionStorage.removeItem(pendingMsgKey);
          } catch (e) {
            // Ignore
          }

          // Clear PWA caches and reload to pick up any frontend changes
          // This ensures rebuilt assets are always served fresh after restart
          const doReload = () => window.location.reload();
          if (typeof caches !== 'undefined') {
            caches.keys().then(names => {
              Promise.all(names.map(name => caches.delete(name))).then(() => {
                console.log('[SW] All caches cleared after server restart, reloading...');
                doReload();
              });
            }).catch(doReload);
          } else {
            doReload();
          }
          break;

        case 'restart_continuation':
          // Server restart is continuing conversations — may arrive multiple times
          // (once per session that was active when restart was triggered).
          // data.source = who triggered the restart (e.g. "ren", "chat_coder", "settings_ui")
          // data.role = "trigger" (initiated the restart) or "bystander" (was just working)
          // data.agent = the agent for this specific session
          console.log(`Restart continuation: session=${data.session_id}, agent=${data.agent}, role=${data.role}, source=${data.source}, reason=${data.reason}`);

          // Reset any stale state first
          pendingDeltas.current.clear();
          pendingMessages.current.clear();

          // Load the session and prepare for continuation
          if (data.session_id) {
            setSessionId(data.session_id);
            fetch(`${API_URL}/chat/history/${data.session_id}`)
              .then(res => res.ok ? res.json() : null)
              .then(chatData => {
                if (chatData) {
                  // Prefer display_messages (has blocks/thinking) over flat messages
                  const msgs = chatData.display_messages || chatData.messages;
                  if (msgs) {
                    setMessages(msgs);
                    console.log(`Loaded ${msgs.length} messages for continuation (display_messages=${'display_messages' in chatData})`);
                  }
                }
              })
              .catch(err => console.warn('Could not load session for continuation:', err));

            // DON'T set status here — let actual streaming events (message_start,
            // block_start, etc.) set the status naturally. If the model already
            // completed before restart, no streaming events will arrive and
            // status correctly stays idle. Setting it here caused a stuck
            // "Processing..." indicator when models were re-invoked unnecessarily.
          }
          break;

        case 'interrupted':
          // Generation was stopped by user
          console.log('Generation interrupted:', data.success ? 'successfully' : 'failed');
          setStatus('idle');
          setStatusText('');
          setActiveTools(new Map());
          setTodos([]);
          // Finalize any streaming messages and insert confirmed injected messages at their positions
          setMessages(prev => {
            let updated = prev.map(m =>
              m.isStreaming ? { ...m, isStreaming: false } : m
            );
            // Same position-based insertion logic as the done handler
            const injectedInfos = injectedMessagesRef.current;
            if (injectedInfos.length > 0) {
              const existingIds = new Set(updated.map(m => m.id));
              const dedupedInfos = injectedInfos.filter(info => !existingIds.has(info.message.id));
              for (let i = dedupedInfos.length - 1; i >= 0; i--) {
                const { message: injMsg, injectionBlockIndex, injectionMessageId } = dedupedInfos[i];
                const targetIdx = updated.findIndex(m => m.id === injectionMessageId);
                if (targetIdx >= 0 && updated[targetIdx].blocks && updated[targetIdx].blocks!.length > 0) {
                  const targetMsg = updated[targetIdx];
                  const blocks = targetMsg.blocks!;
                  if (injectionBlockIndex <= 0) {
                    updated = [...updated.slice(0, targetIdx), injMsg, ...updated.slice(targetIdx)];
                  } else if (injectionBlockIndex >= blocks.length) {
                    updated = [...updated.slice(0, targetIdx + 1), injMsg, ...updated.slice(targetIdx + 1)];
                  } else {
                    const firstHalf: ChatMessage = { id: `${targetMsg.id}-pre-${i}`, role: targetMsg.role, blocks: blocks.slice(0, injectionBlockIndex), isStreaming: false, content: '' };
                    const secondHalf: ChatMessage = { id: `${targetMsg.id}-cont-${i}`, role: targetMsg.role, blocks: blocks.slice(injectionBlockIndex), isStreaming: false, content: '' };
                    updated = [...updated.slice(0, targetIdx), firstHalf, injMsg, secondHalf, ...updated.slice(targetIdx + 1)];
                  }
                } else {
                  const lastAssistantIdx = updated.findLastIndex(m => m.role === 'assistant');
                  if (lastAssistantIdx > 0) {
                    updated = [...updated.slice(0, lastAssistantIdx), injMsg, ...updated.slice(lastAssistantIdx)];
                  } else {
                    updated.push(injMsg);
                  }
                }
              }
              injectedMessagesRef.current = [];
            }
            return updated;
          });
          pendingDeltas.current.clear();
          // Mark remaining queued messages: confirmed ones are cleared, pending ones are not_delivered
          setQueuedMessages(prev => {
            if (prev.length === 0) return prev;
            const remaining = prev.filter(m => m.status !== 'confirmed');
            if (remaining.length === 0) return [];
            return remaining.map(m => m.status !== 'not_delivered' ? { ...m, status: 'not_delivered' as QueuedMessageStatus } : m);
          });
          break;

        case 'message_injected':
          // Server confirmed message was injected mid-stream
          // DON'T insert into messages yet — keep the queued message visible at the bottom
          // during streaming. We'll insert at the exact injection position when the turn completes.
          console.log(`[INJECT] message_injected received:`, JSON.stringify(data.message));
          if (data.message) {
            const injectedMsg: ChatMessage = {
              ...data.message,
              status: 'injected' as MessageStatus,
              injected: true,
            };
            // Capture injection position: find the streaming assistant message and record its current block count
            // Using setMessages callback to read current state without stale closure issues
            setMessages(prev => {
              const streamingMsg = [...prev].reverse().find(m => m.isStreaming && m.role === 'assistant');
              if (!streamingMsg) {
                console.warn(`[INJECT] No streaming assistant message found — cannot determine injection position`);
                // Still store for deferred insertion, will use fallback placement
                if (!injectedMessagesRef.current.some(info => info.message.id === injectedMsg.id)) {
                  injectedMessagesRef.current.push({
                    message: injectedMsg,
                    injectionBlockIndex: -1,
                    injectionMessageId: '',
                  });
                }
                return prev;
              }
              const blockIndex = streamingMsg.blocks?.length ?? 0;
              const messageId = streamingMsg.id;
              console.log(`[INJECT] captured position: block ${blockIndex} of message ${messageId}`);
              // Avoid duplicates
              if (!injectedMessagesRef.current.some(info => info.message.id === injectedMsg.id)) {
                injectedMessagesRef.current.push({
                  message: injectedMsg,
                  injectionBlockIndex: blockIndex,
                  injectionMessageId: messageId,
                });
                console.log(`[INJECT] stored for deferred insertion, count: ${injectedMessagesRef.current.length}`);
              }
              return prev; // Don't modify messages
            });
            // Update queued message status to 'confirmed' (keeps it visible at the bottom)
            setQueuedMessages(prev => prev.map(m =>
              m.id === data.message.id ? { ...m, status: 'confirmed' as QueuedMessageStatus } : m
            ));
          }
          break;

        case 'inject_success':
          // Injection acknowledged by server - mark as confirmed (will be removed when message_injected arrives)
          console.log(`Injection confirmed: ${data.msgId}`);
          setQueuedMessages(prev => prev.map(m =>
            m.id === data.msgId ? { ...m, status: 'confirmed' as QueuedMessageStatus } : m
          ));
          break;

        case 'inject_failed':
          // Injection failed - mark as not delivered so user can copy/dismiss
          console.error(`Injection failed: ${data.error}`);
          setQueuedMessages(prev => prev.map(m =>
            m.id === data.msgId ? { ...m, status: 'not_delivered' as QueuedMessageStatus } : m
          ));
          break;

        case 'agent_notification':
          // Ping mode agent completed and auto-wake triggered
          // Reload chat if it's for our current session
          console.log(`Agent notification: ${data.agent} completed for chat ${data.chat_id}`);
          if (data.chat_id === sessionId && data.has_response) {
            console.log('Reloading chat after ping mode wake-up...');
            fetch(`${API_URL}/chat/history/${sessionId}`)
              .then(res => res.ok ? res.json() : null)
              .then(chatData => {
                if (chatData) {
                  const msgs = chatData.display_messages || chatData.messages;
                  if (msgs) {
                    setMessages(msgs);
                    console.log(`Updated chat with ${msgs.length} messages after agent wake-up`);
                  }
                }
              })
              .catch(err => console.warn('Could not reload chat after agent notification:', err));
          }
          break;

      }
    };

    socket.onclose = () => {
      setConnectionStatus('disconnected');
      reconnectAttempts.current++;
      const delay = Math.min(3000 * reconnectAttempts.current, 30000);
      console.log(`WebSocket closed. Reconnecting in ${delay / 1000}s...`);
      setTimeout(connect, delay);
    };

    socket.onerror = () => {
      setConnectionStatus('disconnected');
    };

    ws.current = socket;
  }, []);

  useEffect(() => {
    if (!enabled) {
      setConnectionStatus('disconnected');
      return;
    }

    connect();

    // Handle visibility changes (tab switching, window dragging between monitors)
    // Don't close WebSocket on visibility change - just let it reconnect if needed
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        if (ws.current?.readyState !== WebSocket.OPEN) {
          // WebSocket dropped while tab was hidden — full reconnect
          console.log('Tab visible again, reconnecting WebSocket...');
          connect();
        } else {
          // WebSocket still open — re-subscribe to get latest state
          // (tool calls, text segments, etc. may have completed while tab was hidden)
          const currentId = sessionIdRef.current;
          if (currentId && currentId !== 'new') {
            console.log('Tab visible again, re-subscribing to', currentId);
            ws.current.send(JSON.stringify({
              action: 'subscribe',
              sessionId: currentId
            }));
          }
        }
      }
      // Don't do anything when hidden - let the connection stay open
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      // Cancel any pending delta flush RAF
      if (rafId.current) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      // Clear handlers before closing to prevent any stray events
      if (ws.current) {
        ws.current.onmessage = null;
        ws.current.onclose = null;
        ws.current.onerror = null;
        ws.current.close();
        ws.current = null;
      }
    };
  }, [connect, enabled]);

  // Handle visibility changes for notification suppression
  const handleVisibilityStateChange = useCallback((state: VisibilityState) => {
    sendVisibilityUpdate(state.isUserActive, sessionId !== 'new' ? sessionId : undefined);
  }, [sendVisibilityUpdate, sessionId]);

  // Use visibility hook
  useVisibility(handleVisibilityStateChange);

  // Visibility heartbeat - send every 60 seconds
  useEffect(() => {
    // Clear any existing heartbeat
    if (visibilityHeartbeatRef.current) {
      clearInterval(visibilityHeartbeatRef.current);
    }

    // Set up new heartbeat
    visibilityHeartbeatRef.current = setInterval(() => {
      const isActive = document.visibilityState === 'visible' && document.hasFocus();
      sendVisibilityUpdate(isActive, sessionId !== 'new' ? sessionId : undefined);
    }, 60000); // 60 seconds

    return () => {
      if (visibilityHeartbeatRef.current) {
        clearInterval(visibilityHeartbeatRef.current);
      }
    };
  }, [sendVisibilityUpdate, sessionId]);

  // FIX BUG 3: Processing state timeout safety net
  // If we're in a processing state with no activity for 30 seconds after page load,
  // it's likely a stale state from before a refresh/restart. Reset to idle.
  // IMPORTANT: Don't timeout during active tool execution (tool_use status) because
  // tools like bash can legitimately take a long time (e.g., waiting for user to review output).
  const processingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastActivityTime = useRef<number>(Date.now());

  // Track activity to prevent false timeouts during legitimate long operations
  useEffect(() => {
    // Update activity timestamp whenever status changes
    lastActivityTime.current = Date.now();
  }, [status, statusText, toolName]);

  useEffect(() => {
    // Clear any existing timeout
    if (processingTimeoutRef.current) {
      clearTimeout(processingTimeoutRef.current);
      processingTimeoutRef.current = null;
    }

    // Don't set timeout if we're idle
    if (status === 'idle') {
      return;
    }

    // CRITICAL FIX: Use much longer timeout during tool_use because tools can legitimately
    // take a long time (e.g., waiting for Claude Code to finish, long bash commands, etc.)
    // Only use short timeout for 'thinking' and 'processing' states which shouldn't stall.
    const timeoutMs = status === 'tool_use' ? 300000 : 30000; // 5 minutes for tools, 30 seconds otherwise

    // If we're in a processing state, set a timeout to auto-reset
    // This only triggers if no 'done' event arrives (stale state scenario)
    processingTimeoutRef.current = setTimeout(() => {
      // Double-check that we haven't received any activity updates
      const timeSinceActivity = Date.now() - lastActivityTime.current;
      if (timeSinceActivity < timeoutMs - 1000) {
        // Activity happened recently, don't reset yet - reschedule
        console.log(`Activity detected ${timeSinceActivity}ms ago, not resetting`);
        return;
      }

      console.log(`Processing state timeout after ${timeoutMs/1000}s - resetting to idle (likely stale state). Status was: ${status}`);
      setStatus('idle');
      setStatusText('');
      setActiveTools(new Map());
      // Finalize any streaming messages
      setMessages(prev => prev.map(m =>
        m.isStreaming ? { ...m, isStreaming: false } : m
      ));
      pendingDeltas.current.clear();
    }, timeoutMs);

    return () => {
      if (processingTimeoutRef.current) {
        clearTimeout(processingTimeoutRef.current);
      }
    };
  }, [status]); // Re-run when status changes - timeout resets on each status change

  // Internal function to actually send a message (bypasses queue check)
  const sendMessageInternal = useCallback((text: string, images?: ChatImageRef[], agent?: string): boolean => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      console.error("Cannot send message: WebSocket not open");
      return false;
    }

    const msgId = Date.now().toString();

    // Add to pending messages queue with status tracking
    const queuedMsg: QueuedMessage = {
      id: msgId,
      content: text,
      sessionId,
      timestamp: Date.now(),
      acknowledged: false,
      status: 'pending'
    };
    pendingMessages.current.set(msgId, queuedMsg);

    // Save pending message in case of unmount during processing
    // This is critical for recovery after page refresh/app close
    try {
      sessionStorage.setItem(pendingMsgKey, JSON.stringify({
        id: msgId,
        content: text,
        sessionId,
        timestamp: Date.now(),
        status: 'pending'
      }));
    } catch (e) {
      console.warn('Could not save pending message:', e);
    }

    // Optimistically add user message with 'pending' status
    setMessages(prev => [...prev, {
      id: msgId,
      role: 'user',
      content: text,
      status: 'pending',
      timestamp: Date.now(),
      images: images,
    }]);
    setStatus('thinking');
    setStatusText('Sending...');
    pendingDeltas.current.clear();

    const wsPayload: Record<string, any> = {
      action: 'message',
      sessionId,
      message: text,
      msgId,
      // CRITICAL: Include preserveChatId to prevent server from creating duplicate files
      // If we have a valid session, tell the server to keep using that chat file
      preserveChatId: sessionId !== 'new' ? sessionId : undefined
    };
    if (images && images.length > 0) {
      wsPayload.images = images;
    }
    // Include agent for new chats only (existing chats use stored agent)
    if (agent && sessionId === 'new') {
      wsPayload.agent = agent;
    }
    ws.current.send(JSON.stringify(wsPayload));

    // Set up ACK timeout - warn if no acknowledgment received
    setTimeout(() => {
      const pending = pendingMessages.current.get(msgId);
      if (pending && !pending.acknowledged) {
        console.warn(`Message ${msgId} not acknowledged after ${ackTimeoutMs}ms - may have been lost`);
        // Mark message as potentially failed in UI
        setMessages(prev => prev.map(m =>
          m.id === msgId ? { ...m, status: 'failed' as MessageStatus } : m
        ));
        setStatusText('Connection issue - retrying...');

        // Attempt to resync with server
        syncWithServer(sessionId, msgId);
      }
    }, ackTimeoutMs);

    return true;
  }, [sessionId]);

  // Inject a message mid-stream (while Claude is working)
  const injectMessage = useCallback((text: string): boolean => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      console.error("Cannot inject message: WebSocket not open");
      return false;
    }

    const msgId = `inject-${Date.now()}`;

    // Add to queued messages for UI display
    const queuedMsg: UserQueuedMessage = {
      id: msgId,
      content: text,
      timestamp: Date.now(),
      status: 'pending'
    };
    setQueuedMessages(prev => [...prev, queuedMsg]);

    // Send injection request to server
    ws.current.send(JSON.stringify({
      action: 'inject',
      sessionId,
      message: text,
      msgId
    }));

    console.log(`Injecting message mid-stream: "${text.slice(0, 50)}..."`);
    return true;
  }, [sessionId]);

  // Send with explicit agent selection (for new chats with non-default agent)
  const sendMessageWithAgent = useCallback((text: string, agent?: string, images?: ChatImageRef[]): boolean => {
    if (status !== 'idle') {
      return injectMessage(text);
    }
    return sendMessageInternal(text, images, agent);
  }, [status, sendMessageInternal, injectMessage]);

  // Public send function - injects messages if Claude is busy, sends normally if idle
  const sendMessage = useCallback((text: string, images?: ChatImageRef[]): boolean => {
    // If Claude is currently responding, inject the message into the stream
    // This allows mid-stream corrections/additions that Claude sees immediately
    // Note: images are not supported for injection (mid-stream)
    if (status !== 'idle') {
      return injectMessage(text);
    }

    // Otherwise, send as a new conversation turn
    return sendMessageInternal(text, images);
  }, [status, sendMessageInternal, injectMessage]);

  // NOTE: We no longer queue messages for post-completion processing.
  // Messages sent while Claude is busy are now INJECTED immediately into
  // the active stream. The queued messages state is used for UI display only.

  // Sync with server to recover from connection issues
  const syncWithServer = useCallback(async (syncSessionId: string, lastMsgId?: string) => {
    if (syncSessionId === 'new') return;

    try {
      const res = await fetch(`${API_URL}/chat/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: syncSessionId,
          last_message_id: lastMsgId
        })
      });

      if (!res.ok) return;

      const data = await res.json();

      if (data.status === 'ok' && data.messages) {
        // Merge server state with local state
        setMessages(prev => {
          // Get all message IDs from server
          const serverMsgIds = new Set(data.messages.map((m: ChatMessage) => m.id));

          // Keep local messages that aren't on server (pending) and add server messages
          const localPending = prev.filter(m => m.status === 'pending' && !serverMsgIds.has(m.id));
          return [...data.messages, ...localPending];
        });

        console.log(`Synced ${data.messages.length} messages from server`);
      }

      if (data.has_pending) {
        console.log(`Server has pending message in status: ${data.pending_status}`);
        setStatusText('Processing...');
      }
    } catch (e) {
      console.warn('Sync failed:', e);
    }
  }, []);

  const editMessage = useCallback((messageId: string, newContent: string): boolean => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      console.error("Cannot edit: WebSocket not open");
      return false;
    }

    setStatus('thinking');
    setStatusText('Re-processing...');
    pendingDeltas.current.clear();

    ws.current.send(JSON.stringify({
      action: 'edit',
      sessionId,
      messageId,
      content: newContent
    }));
    return true;
  }, [sessionId]);

  // Update a form message's status and submitted values (for inline forms)
  const updateFormMessage = useCallback((formId: string, submittedValues: Record<string, any>) => {
    setMessages(prev => prev.map(m =>
      m.formData?.formId === formId
        ? { ...m, formData: { ...m.formData!, status: 'submitted' as const, submittedValues } }
        : m
    ));
  }, []);

  const regenerateMessage = useCallback((messageId: string): boolean => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      console.error("Cannot regenerate: WebSocket not open");
      return false;
    }

    setStatus('thinking');
    setStatusText('Regenerating...');
    pendingDeltas.current.clear();

    ws.current.send(JSON.stringify({
      action: 'regenerate',
      sessionId,
      messageId
    }));
    return true;
  }, [sessionId]);

  const stopGeneration = useCallback((): boolean => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      console.error("Cannot stop: WebSocket not open");
      return false;
    }

    if (status === 'idle') {
      console.log("Nothing to stop - already idle");
      return false;
    }

    console.log("Sending interrupt request...");
    setStatusText('Stopping...');

    ws.current.send(JSON.stringify({
      action: 'interrupt',
      sessionId
    }));
    return true;
  }, [sessionId, status]);

  const startNewChat = useCallback(() => {
    isUserInitiatedLoad.current = true;
    setMessages([]);
    setSessionId('new');
    setStatus('idle');
    setStatusText('');
    setActiveTools(new Map());
    setTodos([]);
    setCurrentAgent(null);
    pendingDeltas.current.clear();
    // Clear any queued/injected messages when starting a new chat
    setQueuedMessages([]);
    injectedMessagesRef.current = [];
    // Guard against stale events from old session arriving before state response
    awaitingStateResponse.current = true;
    localStorage.removeItem(sessionKey);
    // Notify server: unregister from old session, don't redirect to active streams
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        action: 'subscribe',
        sessionId: 'new',
        intent: 'new_chat'
      }));
    }
  }, []);

  const loadChat = useCallback(async (id: string, agentHint?: string | null) => {
    try {
      isUserInitiatedLoad.current = true;
      setSessionId(id);
      // Sync ref immediately so streaming event filter uses the new ID right away
      // (useEffect runs after render, leaving a gap where stale events could leak through)
      sessionIdRef.current = id;

      // Cancel any pending RAF from the previous session
      if (rafId.current) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      pendingDeltas.current.clear();
      // Clear injected messages from previous chat to prevent leaking across chats
      injectedMessagesRef.current = [];

      // Reset all state
      setStatus('idle');
      setStatusText('');
      setActiveTools(new Map());
      setTodos([]);
      // Clear messages immediately to prevent flash of stale content from previous tab
      setMessages([]);
      setQueuedMessages([]);

      // Bug fix: Immediately set agent from caller-provided hint (e.g. from tab data)
      // This prevents the brief flash of wrong agent name while waiting for the
      // WebSocket 'state' response. The server will confirm/correct via the state handler.
      if (agentHint !== undefined) {
        setCurrentAgent(agentHint);
      }

      // Subscribe via WebSocket — this registers us for broadcasts AND returns full state
      // (including active streaming if the chat is still processing)
      // Set guard flag: ignore streaming events until 'state' response arrives.
      // This prevents race conditions where stale/early events corrupt state during switch.
      awaitingStateResponse.current = true;
      if (ws.current && ws.current.readyState === WebSocket.OPEN) {
        ws.current.send(JSON.stringify({
          action: 'subscribe',
          sessionId: id
        }));
        // The 'state' response handler will clear awaitingStateResponse and set messages, status, etc.
      } else {
        // Fallback: fetch via REST if WebSocket not available
        awaitingStateResponse.current = false; // No WS state response coming, clear guard
        const res = await fetch(`${API_URL}/chat/history/${id}`);
        if (!res.ok) throw new Error("Failed to load");
        const data = await res.json();
        setMessages(data.display_messages || data.messages || []);
        setCurrentAgent(data.agent || "ren");
      }
    } catch (e) {
      console.error(e);
      awaitingStateResponse.current = false; // Clear guard on error
      isUserInitiatedLoad.current = false;
    }
  }, []);

  const deleteChat = useCallback(async (chatSessionId: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_URL}/chat/history/${chatSessionId}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        if (chatSessionId === sessionId) {
          startNewChat();
        }
        return true;
      }
      return false;
    } catch (e) {
      console.error(e);
      return false;
    }
  }, [sessionId, startNewChat]);

  // Clear all queued messages (user can cancel them)
  const clearQueuedMessages = useCallback(() => {
    setQueuedMessages([]);
    injectedMessagesRef.current = [];
  }, []);

  // Dismiss a single queued message (e.g. after copy or not-delivered)
  const dismissQueuedMessage = useCallback((id: string) => {
    setQueuedMessages(prev => prev.filter(m => m.id !== id));
  }, []);

  return {
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
    activeTools,
    startNewChat,
    loadChat,
    sessionId,
    connectionStatus,
    queuedMessages,
    clearQueuedMessages,
    dismissQueuedMessage,
    currentAgent,
    sendMessageWithAgent,
    todos,
  };
};
