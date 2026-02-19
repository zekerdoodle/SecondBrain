import { useState, useRef, useCallback, useEffect } from 'react';
import type { ChatMessage, ChatImageRef, MessageStatus } from './types';
import type { ToolCallData } from './components/ToolCallChips';
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
export interface UserQueuedMessage {
  id: string;
  content: string;
  timestamp: number;
  failed?: boolean;  // True if injection failed
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
  // Multi-agent support
  currentAgent: string | null;
  sendMessageWithAgent: (text: string, agent?: string, images?: ChatImageRef[]) => boolean;
  // Tool calls completed during streaming, keyed by the preceding message ID for correct ordering
  streamingToolMap: Map<string, ToolCallData[]>;
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
  // Streaming segments: ordered list of tool-call batches keyed to the message ID they follow.
  // This keeps tool chips interleaved correctly between text segments during streaming.
  const [streamingToolMap, setStreamingToolMap] = useState<Map<string, ToolCallData[]>>(new Map());
  // Stash tool args from tool_start so they're available at tool_end for chip rendering
  const pendingToolArgs = useRef<Map<string, { name: string; args?: string }>>(new Map());
  // Track the ID of the most recently finalized streaming message (set on tool_start)
  const lastFinalizedStreamingMsgId = useRef<string | null>(null);
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

  // Track streaming content separately from final messages
  const streamingContent = useRef<string>('');
  // Track when the last event was a tool_end - next content starts a new message
  const lastEventWasToolEnd = useRef(false);
  // Guard: set after tool_start finalizes streaming content. Prevents the late-arriving
  // SDK 'content' event (complete text block) from creating a duplicate streaming message.
  const segmentFinalizedByToolStart = useRef(false);

  // RAF batching for streaming updates - prevents re-rendering on every delta
  const rafId = useRef<number | null>(null);
  const pendingStreamUpdate = useRef(false);

  // Guard flag: when true, streaming events are ignored until the 'state' response arrives.
  // This prevents the race condition where stale/early events corrupt state during chat switching.
  const awaitingStateResponse = useRef(false);

  const flushStreamingUpdate = useCallback(() => {
    rafId.current = null;
    pendingStreamUpdate.current = false;
    const content = streamingContent.current;
    setMessages(prev => {
      const last = prev[prev.length - 1];
      if (last && last.role === 'assistant' && last.isStreaming) {
        return [...prev.slice(0, -1), { ...last, content }];
      } else {
        return [...prev, {
          id: 'streaming-' + Date.now(),
          role: 'assistant' as const,
          content,
          isStreaming: true
        }];
      }
    });
  }, []);

  const scheduleStreamingUpdate = useCallback(() => {
    if (!pendingStreamUpdate.current) {
      pendingStreamUpdate.current = true;
      rafId.current = requestAnimationFrame(flushStreamingUpdate);
    }
  }, [flushStreamingUpdate]);

  // Message queue for reliability - track pending messages awaiting ACK
  const pendingMessages = useRef<Map<string, QueuedMessage>>(new Map());
  const ackTimeoutMs = 5000; // 5 seconds to receive ACK before showing warning

  // User message queue - messages sent while Claude is responding
  // These will be sent automatically when the current turn completes
  const [queuedMessages, setQueuedMessages] = useState<UserQueuedMessage[]>([]);
  // Flag to trigger queue processing after turn completion
  const shouldProcessQueue = useRef(false);

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
              if (awaitingStateResponse.current && data.messages && Array.isArray(data.messages)) {
                awaitingStateResponse.current = false;
                setMessages(data.messages);
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
      const streamingEventTypes = new Set([
        'content_delta', 'content', 'tool_start', 'tool_end',
        'status', 'thinking_delta', 'done', 'todo_update'
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
          // This handles reconnect, refresh, everything - no sync logic needed
          // Clear the guard flag — state has arrived, streaming events can now be processed
          // Guard cleared — state snapshot received
          awaitingStateResponse.current = false;
          try {
            // CRITICAL: Update session ID to match server
            // Server may redirect us to the active streaming session
            if (data.sessionId && data.sessionId !== 'new') {
              setSessionId(data.sessionId);
            }
            // Capture agent from server state
            if (data.agent !== undefined) {
              setCurrentAgent(data.agent || null);
            }

            // Set messages (server's authoritative copy)
            if (data.messages) {
              // Apply server's authoritative messages
              // If server is processing and has streaming content, append it as streaming message
              if (data.isProcessing && data.streamingContent) {
                const lastMessage = data.messages[data.messages.length - 1];

                // Check for overlap to prevent duplication on reconnect
                // If the last message is an assistant message that overlaps with streaming content,
                // update the last message instead of appending a new streaming message
                const hasOverlap = lastMessage &&
                  lastMessage.role === 'assistant' &&
                  data.streamingContent.length > 0 &&
                  (data.streamingContent.startsWith(lastMessage.content.slice(0, 100)) ||
                   lastMessage.content.startsWith(data.streamingContent.slice(0, 100)));

                if (hasOverlap) {
                  // Update the last message with the full streaming content
                  const updatedMessages = [
                    ...data.messages.slice(0, -1),
                    {
                      ...lastMessage,
                      content: data.streamingContent,
                      isStreaming: true
                    }
                  ];
                  setMessages(updatedMessages);
                } else {
                  // Append as new streaming message
                  const messagesWithStreaming = [
                    ...data.messages,
                    {
                      id: 'streaming-reconnect',
                      role: 'assistant' as const,
                      content: data.streamingContent,
                      isStreaming: true
                    }
                  ];
                  setMessages(messagesWithStreaming);
                }
                streamingContent.current = data.streamingContent;
              } else {
                // No active streaming — set messages as-is
                setMessages(data.messages);
                streamingContent.current = '';
              }
            }

            // Set status from server
            if (data.isProcessing) {
              setStatus(data.status === 'tool_use' ? 'tool_use' : 'thinking');
              setStatusText(data.statusText || 'Processing...');
              // Restore active tools from server state
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
              // Restore todo list from server state (always set, even to empty,
              // so stale todos from a previous chat don't bleed through)
              setTodos(Array.isArray(data.todos) ? data.todos : []);
            } else {
              setStatus('idle');
              setStatusText('');
              setActiveTools(new Map());
              setTodos([]);
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
          // Capture agent from session_init
          if (data.agent !== undefined) {
            setCurrentAgent(data.agent);
          }
          break;

        case 'content_delta':
          // Real-time streaming delta - append to current message
          // Uses requestAnimationFrame batching to avoid re-rendering on every delta
          {
            const text = data.text || '';
            if (text) {
              segmentFinalizedByToolStart.current = false; // New segment content arriving
              // Check if this is after a tool completed
              if (lastEventWasToolEnd.current) {
                lastEventWasToolEnd.current = false;
                streamingContent.current = text;
                // First delta after tool_end needs immediate render to finalize previous messages
                if (rafId.current) cancelAnimationFrame(rafId.current);
                pendingStreamUpdate.current = false;
                setMessages(prev => {
                  const finalized = prev.map(m =>
                    m.isStreaming ? { ...m, isStreaming: false } : m
                  );
                  return [...finalized, {
                    id: 'streaming-' + Date.now(),
                    role: 'assistant',
                    content: text,
                    isStreaming: true
                  }];
                });
              } else {
                streamingContent.current += text;
                // Batch updates - only render once per animation frame
                scheduleStreamingUpdate();
              }
              setStatus('thinking');
              setStatusText('');
            }
          }
          break;

        case 'content':
          // Complete content block - may come after deltas or instead.
          // IMPORTANT: The SDK emits AssistantMessage (which yields 'content')
          // AFTER tool_start. If tool_start already finalized this text from
          // deltas, skip it to prevent creating a duplicate streaming message.
          {
            const text = data.text || '';
            if (!text) break;

            // Guard: skip if tool_start already finalized this content from deltas
            if (segmentFinalizedByToolStart.current) {
              segmentFinalizedByToolStart.current = false;
              break; // Already finalized - skip to prevent duplication
            }

            // After tool_end, this is a NEW message - always start fresh
            if (lastEventWasToolEnd.current) {
              lastEventWasToolEnd.current = false;
              streamingContent.current = text;
              setMessages(prev => {
                const finalized = prev.map(m =>
                  m.isStreaming ? { ...m, isStreaming: false } : m
                );
                return [...finalized, {
                  id: 'streaming-' + Date.now(),
                  role: 'assistant',
                  content: text,
                  isStreaming: true
                }];
              });
            } else if (!streamingContent.current) {
              // No streaming content yet - start a new message
              streamingContent.current = text;
              setMessages(prev => {
                const last = prev[prev.length - 1];
                if (last && last.role === 'assistant' && last.isStreaming) {
                  return [...prev.slice(0, -1), { ...last, content: text }];
                } else {
                  return [...prev, {
                    id: 'streaming-' + Date.now(),
                    role: 'assistant',
                    content: text,
                    isStreaming: true
                  }];
                }
              });
            }
            // If streamingContent already has content, this is just a confirmation
            // of what we already have from deltas - no action needed

            setStatus('thinking');
            setStatusText('');
          }
          break;

        case 'tool_start': {
          // Flush any pending streaming update before tool starts
          if (rafId.current) {
            cancelAnimationFrame(rafId.current);
            rafId.current = null;
          }
          if (pendingStreamUpdate.current) {
            pendingStreamUpdate.current = false;
            flushStreamingUpdate();
          }
          // Finalize any current streaming content before tool starts
          // Capture the ID of the message being finalized so tool chips attach to it
          setMessages(prev => {
            for (const m of prev) {
              if (m.isStreaming) {
                lastFinalizedStreamingMsgId.current = m.id;
                break;
              }
            }
            return prev.map(m =>
              m.isStreaming ? { ...m, isStreaming: false } : m
            );
          });
          // IMPORTANT: Reset streaming content so post-tool content starts fresh
          streamingContent.current = '';
          // Guard: the SDK 'content' event (complete text block) arrives AFTER tool_start
          // because AssistantMessage is emitted after all streaming events. Without this
          // guard, the content event would see empty streamingContent and create a duplicate.
          segmentFinalizedByToolStart.current = true;
          setStatus('tool_use');
          // Add/merge this tool into active tools map
          const startToolId = data.id || `tool-${Date.now()}`;
          const startToolName = data.name === 'system_log' ? 'System' : data.name;
          const startToolArgs = data.args;
          // Stash args for chip rendering when tool_end fires
          pendingToolArgs.current.set(startToolId, { name: startToolName, args: startToolArgs });
          setActiveTools(prev => {
            const next = new Map(prev);
            const existing = next.get(startToolId);
            next.set(startToolId, {
              id: startToolId,
              name: startToolName,
              args: startToolArgs || existing?.args,
              summary: startToolArgs ? extractToolSummary(startToolName, startToolArgs) : existing?.summary,
              startedAt: existing?.startedAt || Date.now()
            });
            return next;
          });
          // Don't set statusText here - let Chat.tsx use getToolDisplayName() for friendly names
          setStatusText('');
          break;
        }

        case 'tool_end': {
          // Mark that the next content event should be a NEW message
          lastEventWasToolEnd.current = true;
          segmentFinalizedByToolStart.current = false; // Tool done, clear guard
          // Build a completed tool chip from stashed args + tool_end data
          const endToolId = data.id;
          const stashed = endToolId ? pendingToolArgs.current.get(endToolId) : undefined;
          if (stashed && endToolId) {
            let parsedArgs: Record<string, any> = {};
            if (stashed.args) {
              try { parsedArgs = JSON.parse(stashed.args); } catch { /* leave empty */ }
            }
            const toolData: ToolCallData = {
              id: `streaming-tc-${endToolId}`,
              tool_name: stashed.name,
              tool_id: endToolId,
              args: parsedArgs,
              output_summary: data.output ? String(data.output).slice(0, 200) : undefined,
              is_error: data.is_error,
            };
            // Attach to the message that was finalized when tool_start fired
            const anchorId = lastFinalizedStreamingMsgId.current || '__pre__';
            setStreamingToolMap(prev => {
              const next = new Map(prev);
              const existing = next.get(anchorId) || [];
              next.set(anchorId, [...existing, toolData]);
              return next;
            });
            pendingToolArgs.current.delete(endToolId);
          }
          // Remove this specific tool from active tools
          setActiveTools(prev => {
            const next = new Map(prev);
            if (endToolId) {
              next.delete(endToolId);
            }
            if (next.size === 0) {
              // No more tools running - transition to processing
              setStatus('processing');
              setStatusText('Processing...');
            }
            return next;
          });
          break;
        }

        case 'status':
          setStatusText(data.text || '');
          // Heartbeats may send identical text; React skips re-render for
          // same-value setState, so the useEffect tracking lastActivityTime
          // never fires. Update the ref directly to prevent the 5-min timeout
          // from resetting state during long-running tools.
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
          // Reset streaming for the new response
          streamingContent.current = '';
          break;

        case 'done':
          // Flush any pending streaming update before finalizing
          if (rafId.current) {
            cancelAnimationFrame(rafId.current);
            rafId.current = null;
          }
          if (pendingStreamUpdate.current) {
            pendingStreamUpdate.current = false;
            flushStreamingUpdate();
          }

          // Finalize the conversation turn
          setStatus('idle');
          setStatusText('');
          setActiveTools(new Map());
          setTodos([]);

          // Convert streaming tool pills into persistent tool_call messages
          // BEFORE clearing the streaming tool map. This ensures tool pills
          // survive the done transition even if the server's done payload
          // is missing them (e.g., fast tool calls where events were lost).
          // The server sync (applyServerMessages) will replace these with
          // authoritative data if available.
          setStreamingToolMap(prevToolMap => {
            if (prevToolMap.size > 0) {
              setMessages(prev => {
                const newMessages: any[] = [...prev];
                // For each anchored message, insert tool_call messages after it
                for (const [anchorId, tools] of prevToolMap.entries()) {
                  if (anchorId === '__pre__') {
                    // Tools before any message — insert after the first assistant message
                    const firstAssistantIdx = newMessages.findIndex((m: any) => m.role === 'assistant');
                    if (firstAssistantIdx >= 0) {
                      const toolMsgs = tools.map(tc => ({
                        id: tc.id,
                        role: 'tool_call',
                        content: '',
                        tool_name: tc.tool_name,
                        tool_id: tc.tool_id,
                        args: tc.args,
                        output_summary: tc.output_summary,
                        is_error: tc.is_error,
                        hidden: true,
                      }));
                      newMessages.splice(firstAssistantIdx + 1, 0, ...toolMsgs);
                    }
                  } else {
                    // Find the anchor message and insert tool_calls after it
                    const anchorIdx = newMessages.findIndex((m: any) => m.id === anchorId);
                    if (anchorIdx >= 0) {
                      const toolMsgs = tools.map(tc => ({
                        id: tc.id,
                        role: 'tool_call',
                        content: '',
                        tool_name: tc.tool_name,
                        tool_id: tc.tool_id,
                        args: tc.args,
                        output_summary: tc.output_summary,
                        is_error: tc.is_error,
                        hidden: true,
                      }));
                      newMessages.splice(anchorIdx + 1, 0, ...toolMsgs);
                    }
                  }
                }
                return newMessages;
              });
            }
            return new Map();
          });
          lastFinalizedStreamingMsgId.current = null;
          pendingToolArgs.current.clear();

          // Finalize any streaming messages and mark all messages as complete
          setMessages(prev => prev.map(m => {
            const updates: Partial<ChatMessage> = {};
            if (m.isStreaming) updates.isStreaming = false;
            if (m.status && m.status !== 'complete') updates.status = 'complete';
            return Object.keys(updates).length > 0 ? { ...m, ...updates } : m;
          }));

          streamingContent.current = '';
          lastEventWasToolEnd.current = false;
          segmentFinalizedByToolStart.current = false;

          // Clear pending messages queue - conversation turn complete
          pendingMessages.current.clear();

          // Signal that we should process any queued user messages
          shouldProcessQueue.current = true;

          // Clear pending message - it's now saved on server
          try {
            sessionStorage.removeItem(pendingMsgKey);
            // Save last sync point for recovery
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

          // Sync with server's authoritative messages to recover any segments
          // that may have been lost during streaming (e.g., pre-tool text segments).
          // If the done event includes messages directly (e.g. wake-up handler),
          // use them immediately to avoid a fetch round-trip and tool chip flicker.
          {
            const syncMessages = data.messages;
            const syncId = data.sessionId || sessionId;

            const applyServerMessages = (serverMessages: ChatMessage[]) => {
              setMessages(prev => {
                // Build lookup of client messages by ID for merging
                const clientById = new Map(prev.map(m => [m.id, m]));
                // Also index client form messages by formId for cross-ID matching
                const clientFormByFormId = new Map<string, ChatMessage>();
                for (const m of prev) {
                  if (m.formData?.formId) {
                    clientFormByFormId.set(m.formData.formId, m);
                  }
                }

                // Collect client-side tool_call messages that the server doesn't have.
                // These were generated from streaming tool pills as a defensive fallback.
                const serverIds = new Set(serverMessages.map((m: any) => m.id));
                const serverHasToolCalls = serverMessages.some((m: any) => m.role === 'tool_call');
                const clientToolCalls: any[] = [];
                if (!serverHasToolCalls) {
                  for (const m of prev) {
                    if ((m as any).role === 'tool_call' && !serverIds.has(m.id)) {
                      clientToolCalls.push(m);
                    }
                  }
                }

                // Use server messages as base, merging in client-only fields
                const result = serverMessages.map((serverMsg: ChatMessage) => {
                  const clientMsg = clientById.get(serverMsg.id);
                  if (clientMsg) {
                    // Merge: server content is authoritative, keep client UI state
                    return {
                      ...serverMsg,
                      status: clientMsg.status || serverMsg.status,
                      formData: clientMsg.formData || serverMsg.formData
                    };
                  }
                  // For form messages: match by formId even if IDs differ
                  if (serverMsg.formData?.formId) {
                    const clientForm = clientFormByFormId.get(serverMsg.formData.formId);
                    if (clientForm?.formData) {
                      // Preserve client-side submission status
                      return {
                        ...serverMsg,
                        formData: clientForm.formData.status === 'submitted'
                          ? clientForm.formData
                          : serverMsg.formData
                      };
                    }
                  }
                  return serverMsg;
                });

                // If server didn't include tool_calls but we captured them from streaming,
                // splice them back in at the right positions (after last assistant message
                // that precedes the user message they were anchored to, or at end)
                if (clientToolCalls.length > 0) {
                  // Find the last assistant message index for each tool_call
                  // Simple heuristic: insert all client tool_calls before the final
                  // assistant message (they likely belong between the two text segments)
                  const lastAssistantIdx = result.reduce((acc: number, m: any, i: number) =>
                    m.role === 'assistant' ? i : acc, -1);
                  if (lastAssistantIdx > 0) {
                    // Insert before the last assistant message
                    result.splice(lastAssistantIdx, 0, ...clientToolCalls);
                  } else {
                    // Fallback: append at end
                    result.push(...clientToolCalls);
                  }
                }

                return result;
              });
            };

            if (syncMessages) {
              // Messages included in done event — apply immediately (no fetch needed)
              applyServerMessages(syncMessages);
            } else if (syncId && syncId !== 'new') {
              // Fetch from server (standard streaming path)
              fetch(`${API_URL}/chat/history/${syncId}`)
                .then(res => res.ok ? res.json() : null)
                .then(chatData => {
                  if (chatData?.messages) {
                    applyServerMessages(chatData.messages);
                  }
                })
                .catch(() => {
                  // Ignore - client state is the fallback
                });
            }
          }
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
          setStreamingToolMap(new Map()); lastFinalizedStreamingMsgId.current = null;
          pendingToolArgs.current.clear();
          streamingContent.current = '';
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
          // Any pending work was lost when server restarted. The server will have
          // cleared stale WAL entries, so we must reset client state to match.
          setStatus('idle');
          setStatusText('');
          setActiveTools(new Map());
          setStreamingToolMap(new Map()); lastFinalizedStreamingMsgId.current = null;
          pendingToolArgs.current.clear();
          setTodos([]);
          streamingContent.current = '';
          lastEventWasToolEnd.current = false;
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
          streamingContent.current = '';
          lastEventWasToolEnd.current = false;
          pendingMessages.current.clear();

          // Load the session and prepare for continuation
          if (data.session_id) {
            setSessionId(data.session_id);
            fetch(`${API_URL}/chat/history/${data.session_id}`)
              .then(res => res.ok ? res.json() : null)
              .then(chatData => {
                if (chatData?.messages) {
                  // FIX BUG 2: Replace messages entirely to prevent duplicates
                  setMessages(chatData.messages);
                  console.log(`Loaded ${chatData.messages.length} messages for continuation`);
                }
              })
              .catch(err => console.warn('Could not load session for continuation:', err));

            // Show that we're processing the continuation
            // This is legitimate because Claude IS actively working
            setStatus('thinking');
            const sourceLabel = data.source === 'settings_ui' ? 'Settings UI' : data.source;
            setStatusText(`Resuming after restart (by ${sourceLabel})...`);
          }
          break;

        case 'interrupted':
          // Generation was stopped by user
          console.log('Generation interrupted:', data.success ? 'successfully' : 'failed');
          setStatus('idle');
          setStatusText('');
          setActiveTools(new Map());
          setStreamingToolMap(new Map()); lastFinalizedStreamingMsgId.current = null;
          pendingToolArgs.current.clear();
          setTodos([]);
          // Finalize any streaming messages
          setMessages(prev => prev.map(m =>
            m.isStreaming ? { ...m, isStreaming: false } : m
          ));
          streamingContent.current = '';
          lastEventWasToolEnd.current = false;
          // Clear queued/injected messages on interrupt
          setQueuedMessages([]);
          break;

        case 'message_injected':
          // Server confirmed message was injected mid-stream
          // Add it to the message list (server already saved it)
          console.log(`Message injected: ${data.message?.content?.slice(0, 50)}`);
          if (data.message) {
            setMessages(prev => {
              // Check if already exists
              if (prev.some(m => m.id === data.message.id)) {
                return prev;
              }
              return [...prev, {
                ...data.message,
                status: 'injected' as MessageStatus
              }];
            });
            // Remove from queued messages (UI display)
            setQueuedMessages(prev => prev.filter(m => m.id !== data.message.id));
          }
          break;

        case 'inject_success':
          // Our injection was successful - remove from queue display
          console.log(`Injection confirmed: ${data.msgId}`);
          setQueuedMessages(prev => prev.filter(m => m.id !== data.msgId));
          break;

        case 'inject_failed':
          // Injection failed - show error and keep in queue for retry or manual action
          console.error(`Injection failed: ${data.error}`);
          // Mark the queued message as failed
          setQueuedMessages(prev => prev.map(m =>
            m.id === data.msgId ? { ...m, failed: true } : m
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
                if (chatData?.messages) {
                  setMessages(chatData.messages);
                  console.log(`Updated chat with ${chatData.messages.length} messages after agent wake-up`);
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
      // Cancel any pending streaming RAF
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
      streamingContent.current = '';
      lastEventWasToolEnd.current = false;
      segmentFinalizedByToolStart.current = false;
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
    streamingContent.current = '';
    lastEventWasToolEnd.current = false;
    segmentFinalizedByToolStart.current = false;

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
      timestamp: Date.now()
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
    streamingContent.current = '';

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
    streamingContent.current = '';

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
    setStreamingToolMap(new Map()); lastFinalizedStreamingMsgId.current = null;
    pendingToolArgs.current.clear();
    setTodos([]);
    setCurrentAgent(null);
    streamingContent.current = '';
    lastEventWasToolEnd.current = false;
    segmentFinalizedByToolStart.current = false;
    // Clear any queued messages when starting a new chat
    setQueuedMessages([]);
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

      // Bug fix: Cancel any pending RAF from the previous session's streaming
      // Without this, a scheduled RAF flush could fire after the session switch
      // and corrupt the new session's messages with stale streaming content
      if (rafId.current) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      pendingStreamUpdate.current = false;

      // Reset streaming state before loading
      setStatus('idle');
      setStatusText('');
      setActiveTools(new Map());
      setStreamingToolMap(new Map()); lastFinalizedStreamingMsgId.current = null;
      pendingToolArgs.current.clear();
      setTodos([]);
      streamingContent.current = '';
      lastEventWasToolEnd.current = false;
      segmentFinalizedByToolStart.current = false;
      // Clear messages immediately to prevent flash of stale content from previous tab
      setMessages([]);

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
        setMessages(data.messages || []);
        setCurrentAgent(data.agent || null);
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
    currentAgent,
    sendMessageWithAgent,
    streamingToolMap,
    todos,
  };
};
