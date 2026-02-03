import { useState, useRef, useCallback, useEffect } from 'react';
import type { ChatMessage, MessageStatus } from './types';
import { WS_URL, API_URL } from './config';
import { useVisibility, type VisibilityState } from './hooks/useVisibility';

const SESSION_KEY = 'second_brain_session_id';
const PENDING_MSG_KEY = 'second_brain_pending_message';
const LAST_SYNC_KEY = 'second_brain_last_sync';

// Default base overhead (fetched dynamically on load)
const DEFAULT_BASE_OVERHEAD = 11000;

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

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';
export type ChatStatus = 'idle' | 'thinking' | 'tool_use' | 'processing';

export interface TokenUsage {
  input: number;
  output: number;
  total: number;
  contextPercent?: number;  // Percentage toward auto-compaction (0-100+)
  actualContext?: number;   // Actual context tokens (input + cache_read)
}

export interface ClaudeHook {
  messages: ChatMessage[];
  sendMessage: (text: string) => boolean;
  editMessage: (messageId: string, newContent: string) => boolean;
  updateFormMessage: (formId: string, submittedValues: Record<string, any>) => void;
  regenerateMessage: (messageId: string) => boolean;
  stopGeneration: () => boolean;
  deleteChat: (sessionId: string) => Promise<boolean>;
  status: ChatStatus;
  statusText: string;
  toolName: string | null;
  startNewChat: () => void;
  loadChat: (id: string) => Promise<void>;
  sessionId: string;
  connectionStatus: ConnectionStatus;
  tokenUsage: TokenUsage | null;
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

export interface ClaudeOptions {
  onScheduledTaskComplete?: (data: { session_id: string; title: string }) => void;
  onChatTitleUpdate?: (data: { session_id: string; title: string; confidence: number }) => void;
  onNewMessageNotification?: (data: NotificationData) => void;
  onFormRequest?: (data: FormRequestData) => void;
}

export const useClaude = (options: ClaudeOptions = {}): ClaudeHook => {
  // Messages come from server only - no localStorage
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string>(() => {
    return localStorage.getItem(SESSION_KEY) || 'new';
  });

  const [status, setStatus] = useState<ChatStatus>('idle');
  const [statusText, setStatusText] = useState<string>('');
  const [toolName, setToolName] = useState<string | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);

  // Base overhead for context window (system prompt, tools, memory)
  const baseOverhead = useRef<number>(DEFAULT_BASE_OVERHEAD);

  const ws = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);

  // Visibility tracking for notifications
  const visibilityHeartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Track streaming content separately from final messages
  const streamingContent = useRef<string>('');
  // Track when the last event was a tool_end - next content starts a new message
  const lastEventWasToolEnd = useRef(false);

  // Message queue for reliability - track pending messages awaiting ACK
  const pendingMessages = useRef<Map<string, QueuedMessage>>(new Map());
  const ackTimeoutMs = 5000; // 5 seconds to receive ACK before showing warning

  // Only persist sessionId
  useEffect(() => {
    localStorage.setItem(SESSION_KEY, sessionId);
  }, [sessionId]);

  // Fetch base overhead on mount (system prompt, tools, memory budget)
  useEffect(() => {
    fetch(`${API_URL}/context-overhead`)
      .then(res => res.json())
      .then(data => {
        if (data.total_tokens) {
          baseOverhead.current = data.total_tokens;
          console.log(`Base context overhead: ${data.total_tokens} tokens (${data.percentage_of_200k}%)`);

          // Initialize token usage for new chats with base overhead
          if (sessionId === 'new' && !tokenUsage) {
            // Calculate context percentage (compaction at 95% of 200k = 190k)
            const COMPACTION_THRESHOLD = 190000;
            const contextPercent = (data.total_tokens / COMPACTION_THRESHOLD) * 100;
            setTokenUsage({
              input: data.total_tokens,
              output: 0,
              total: data.total_tokens,
              contextPercent: contextPercent,
              actualContext: data.total_tokens
            });
          }
        }
      })
      .catch(err => {
        console.warn('Could not fetch context overhead:', err);
      });
  }, []); // Only on mount

  // Recover pending message on mount (if component unmounted during send)
  // This is a BACKUP mechanism - the primary sync happens in WebSocket onopen
  // This handles edge cases where WebSocket connection fails or takes too long
  useEffect(() => {
    const recoverPendingMessage = async () => {
      try {
        const pendingStr = sessionStorage.getItem(PENDING_MSG_KEY);
        if (!pendingStr) return;

        const pending = JSON.parse(pendingStr);
        // Only recover if it's recent (within last 5 minutes) and for this session
        const isRecent = Date.now() - pending.timestamp < 300000; // 5 minutes
        const isSameSession = pending.sessionId === sessionId || sessionId === 'new';

        if (!isRecent || !isSameSession) {
          console.log('Pending message too old or wrong session, clearing');
          sessionStorage.removeItem(PENDING_MSG_KEY);
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
                sessionStorage.removeItem(PENDING_MSG_KEY);
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
        sessionStorage.removeItem(PENDING_MSG_KEY);
      } catch (e) {
        console.warn('Error recovering pending message:', e);
        sessionStorage.removeItem(PENDING_MSG_KEY);
      }
    };

    // Small delay to let WebSocket connect first
    const timeoutId = setTimeout(recoverPendingMessage, 500);
    return () => clearTimeout(timeoutId);
  }, []); // Only on mount

  // Track if this is a user-initiated session change (vs automatic)
  const isUserInitiatedLoad = useRef(false);

  // Load chat from server when sessionId changes
  // NOTE: Messages are loaded by the WebSocket onopen handler to ensure single source of truth.
  // This effect only handles user-initiated chat switches (loadChat, startNewChat) where
  // we need to clear/update state before WebSocket sync happens.
  useEffect(() => {
    if (sessionId && sessionId !== 'new') {
      // For user-initiated loads (clicking a chat in history), load messages immediately
      // This provides faster feedback than waiting for WebSocket sync
      if (isUserInitiatedLoad.current) {
        fetch(`${API_URL}/chat/history/${sessionId}`)
          .then(res => {
            if (res.ok) return res.json();
            throw new Error("Chat not found");
          })
          .then(data => {
            if (data.messages && Array.isArray(data.messages)) {
              setMessages(data.messages);
            }
            // Load cumulative token usage from saved chat data
            const COMPACTION_THRESHOLD = 190000;
            if (data.cumulative_usage) {
              const inputTokens = data.cumulative_usage.input_tokens || 0;
              const actualContext = data.cumulative_usage.actual_context || inputTokens;
              const contextPercent = data.cumulative_usage.context_percent ||
                (actualContext / COMPACTION_THRESHOLD) * 100;

              setTokenUsage({
                input: inputTokens,
                output: data.cumulative_usage.output_tokens || 0,
                total: data.cumulative_usage.total_tokens || 0,
                contextPercent: contextPercent,
                actualContext: actualContext
              });
            } else if (data.messages && data.messages.length > 0) {
              const totalChars = data.messages.reduce((acc: number, msg: { content?: string }) =>
                acc + (msg.content?.length || 0), 0);
              const messageTokens = Math.round(totalChars / 4);
              const estimatedTotal = messageTokens + baseOverhead.current;
              const contextPercent = (estimatedTotal / COMPACTION_THRESHOLD) * 100;
              setTokenUsage({
                input: Math.round(estimatedTotal * 0.6),
                output: Math.round(messageTokens * 0.4),
                total: estimatedTotal,
                contextPercent: contextPercent,
                actualContext: estimatedTotal
              });
            }
            isUserInitiatedLoad.current = false;
          })
          .catch(err => {
            console.warn("Could not load chat:", err);
            setMessages([]);
            isUserInitiatedLoad.current = false;
          });
      }
      // For automatic loads (page refresh), WebSocket onopen will handle loading
      // This prevents duplicate fetches and ensures consistent state
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
      const currentSessionId = localStorage.getItem(SESSION_KEY) || sessionId;

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

      switch (data.type) {
        case 'state':
          // SERVER IS THE SOURCE OF TRUTH - just render what it sends
          // This handles reconnect, refresh, everything - no sync logic needed
          try {
            // CRITICAL: Update session ID to match server
            // Server may redirect us to the active streaming session
            if (data.sessionId && data.sessionId !== 'new') {
              setSessionId(data.sessionId);
            }

            // Set messages (server's authoritative copy)
            if (data.messages) {
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
                setMessages(data.messages);
                streamingContent.current = '';
              }
            }

            // Set status from server
            if (data.isProcessing) {
              setStatus(data.status === 'tool_use' ? 'tool_use' : 'thinking');
              setStatusText(data.statusText || 'Processing...');
              setToolName(data.toolName || null);
            } else {
              setStatus('idle');
              setStatusText('');
              setToolName(null);
            }

            // Update token usage if provided
            if (data.cumulative_usage) {
              const COMPACTION_THRESHOLD = 190000;
              const inputTokens = data.cumulative_usage.input_tokens || 0;
              const actualContext = data.cumulative_usage.actual_context || inputTokens;
              const contextPercent = data.cumulative_usage.context_percent ||
                (actualContext / COMPACTION_THRESHOLD) * 100;
              setTokenUsage({
                input: inputTokens,
                output: data.cumulative_usage.output_tokens || 0,
                total: data.cumulative_usage.total_tokens || 0,
                contextPercent: contextPercent,
                actualContext: actualContext
              });
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
          break;

        case 'content_delta':
          // Real-time streaming delta - append to current message
          {
            const text = data.text || '';
            if (text) {
              // Check if this is after a tool completed
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
              } else {
                streamingContent.current += text;
                setMessages(prev => {
                  const last = prev[prev.length - 1];
                  if (last && last.role === 'assistant' && last.isStreaming) {
                    return [...prev.slice(0, -1), { ...last, content: streamingContent.current }];
                  } else {
                    return [...prev, {
                      id: 'streaming-' + Date.now(),
                      role: 'assistant',
                      content: streamingContent.current,
                      isStreaming: true
                    }];
                  }
                });
              }
              setStatus('thinking');
              setStatusText('');
            }
          }
          break;

        case 'content':
          // Complete content block - may come after deltas or instead
          {
            const text = data.text || '';
            if (!text) break;

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

        case 'tool_start':
          // Finalize any current streaming content before tool starts
          setMessages(prev => prev.map(m =>
            m.isStreaming ? { ...m, isStreaming: false } : m
          ));
          // IMPORTANT: Reset streaming content so post-tool content starts fresh
          streamingContent.current = '';
          setStatus('tool_use');
          setToolName(data.name === 'system_log' ? 'System' : data.name);
          // Don't set statusText here - let Chat.tsx use getToolDisplayName() for friendly names
          setStatusText('');
          break;

        case 'tool_end':
          // Mark that the next content event should be a NEW message
          lastEventWasToolEnd.current = true;
          setStatus('processing');
          setToolName(null);
          setStatusText('Processing...');
          break;

        case 'status':
          setStatusText(data.text || '');
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
          // Finalize the conversation turn
          setStatus('idle');
          setStatusText('');
          setToolName(null);

          // Finalize any streaming messages and mark all messages as complete
          setMessages(prev => prev.map(m => {
            const updates: Partial<ChatMessage> = {};
            if (m.isStreaming) updates.isStreaming = false;
            if (m.status && m.status !== 'complete') updates.status = 'complete';
            return Object.keys(updates).length > 0 ? { ...m, ...updates } : m;
          }));

          streamingContent.current = '';
          lastEventWasToolEnd.current = false;

          // Clear pending messages queue - conversation turn complete
          pendingMessages.current.clear();

          // Clear pending message - it's now saved on server
          try {
            sessionStorage.removeItem(PENDING_MSG_KEY);
            // Save last sync point for recovery
            if (data.sessionId) {
              sessionStorage.setItem(LAST_SYNC_KEY, JSON.stringify({
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

          // Don't reload from server - keep the nicely segmented client view
          // Server has the authoritative copy saved, client has the display copy
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
          streamingContent.current = '';
          break;

        case 'result_meta':
          // Update token usage from the completed turn
          if (data.usage) {
            setTokenUsage({
              input: data.usage.input_tokens || 0,
              output: data.usage.output_tokens || 0,
              total: data.usage.total_tokens || 0,
              contextPercent: data.context?.percent_until_compaction,
              actualContext: data.context?.actual_tokens
            });
          }
          break;

        case 'scheduled_task_complete':
          console.log(`Scheduled task complete: ${data.title} (session: ${data.session_id})`);
          options.onScheduledTaskComplete?.({ session_id: data.session_id, title: data.title });
          break;

        case 'new_message_notification':
          console.log(`New message notification: ${data.preview?.slice(0, 50)} (chat: ${data.chatId})`);
          options.onNewMessageNotification?.({
            chatId: data.chatId,
            preview: data.preview || '',
            critical: data.critical || false,
            playSound: data.playSound ?? true
          });
          break;

        case 'chat_title_update':
          console.log(`Chat title updated: "${data.title}" (session: ${data.session_id}, confidence: ${data.confidence})`);
          options.onChatTitleUpdate?.({ session_id: data.session_id, title: data.title, confidence: data.confidence });
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

        case 'server_restarted':
          // Server was restarted - this is a clean slate
          console.log('Server was restarted:', data.message);
          console.log('Previous active sessions:', data.active_sessions);

          // FIX BUG 3: CRITICAL - Reset ALL processing state on server restart
          // Any pending work was lost when server restarted. The server will have
          // cleared stale WAL entries, so we must reset client state to match.
          setStatus('idle');
          setStatusText('');
          setToolName(null);
          streamingContent.current = '';
          lastEventWasToolEnd.current = false;
          pendingMessages.current.clear();

          // Clear any pending message in sessionStorage too
          // It's stale after server restart
          try {
            sessionStorage.removeItem(PENDING_MSG_KEY);
          } catch (e) {
            // Ignore
          }

          // Reload our session from server to get authoritative state
          if (sessionId && sessionId !== 'new') {
            console.log(`Reloading session ${sessionId} after server restart`);
            fetch(`${API_URL}/chat/history/${sessionId}`)
              .then(res => res.ok ? res.json() : null)
              .then(chatData => {
                if (chatData?.messages) {
                  // FIX BUG 2: Replace messages entirely to prevent duplicates
                  // Server state is authoritative after restart
                  setMessages(chatData.messages);
                  console.log(`Restored ${chatData.messages.length} messages from server`);
                }
                // Ensure we stay in idle state - don't let any stale state slip through
                setStatus('idle');
                setStatusText('');
              })
              .catch(err => console.warn('Could not reload session after restart:', err));
          }
          break;

        case 'restart_continuation':
          // Claude-initiated restart is continuing the conversation
          // This is DIFFERENT from server_restarted - here Claude is actively resuming work
          console.log('Restart continuation:', data.message);
          console.log('Continuing session:', data.session_id);

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
            setStatusText('Continuing after restart...');
          }
          break;

        case 'interrupted':
          // Generation was stopped by user
          console.log('Generation interrupted:', data.success ? 'successfully' : 'failed');
          setStatus('idle');
          setStatusText('');
          setToolName(null);
          // Finalize any streaming messages
          setMessages(prev => prev.map(m =>
            m.isStreaming ? { ...m, isStreaming: false } : m
          ));
          streamingContent.current = '';
          lastEventWasToolEnd.current = false;
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
    connect();

    // Handle visibility changes (tab switching, window dragging between monitors)
    // Don't close WebSocket on visibility change - just let it reconnect if needed
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        // When becoming visible again, check if we need to reconnect
        if (ws.current?.readyState !== WebSocket.OPEN) {
          console.log('Tab visible again, reconnecting WebSocket...');
          connect();
        }
      }
      // Don't do anything when hidden - let the connection stay open
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      // Clear handlers before closing to prevent any stray events
      if (ws.current) {
        ws.current.onmessage = null;
        ws.current.onclose = null;
        ws.current.onerror = null;
        ws.current.close();
      }
    };
  }, [connect]);

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
      setToolName(null);
      // Finalize any streaming messages
      setMessages(prev => prev.map(m =>
        m.isStreaming ? { ...m, isStreaming: false } : m
      ));
      streamingContent.current = '';
      lastEventWasToolEnd.current = false;
    }, timeoutMs);

    return () => {
      if (processingTimeoutRef.current) {
        clearTimeout(processingTimeoutRef.current);
      }
    };
  }, [status]); // Re-run when status changes - timeout resets on each status change

  const sendMessage = useCallback((text: string): boolean => {
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
      sessionStorage.setItem(PENDING_MSG_KEY, JSON.stringify({
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
      timestamp: Date.now()
    }]);
    setStatus('thinking');
    setStatusText('Sending...');
    streamingContent.current = '';
    lastEventWasToolEnd.current = false;

    ws.current.send(JSON.stringify({
      action: 'message',
      sessionId,
      message: text,
      msgId,
      // CRITICAL: Include preserveChatId to prevent server from creating duplicate files
      // If we have a valid session, tell the server to keep using that chat file
      preserveChatId: sessionId !== 'new' ? sessionId : undefined
    }));

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
    setToolName(null);
    // Initialize with base overhead instead of null
    const COMPACTION_THRESHOLD = 190000;
    const contextPercent = (baseOverhead.current / COMPACTION_THRESHOLD) * 100;
    setTokenUsage({
      input: baseOverhead.current,
      output: 0,
      total: baseOverhead.current,
      contextPercent: contextPercent,
      actualContext: baseOverhead.current
    });
    streamingContent.current = '';
    lastEventWasToolEnd.current = false;
    localStorage.removeItem(SESSION_KEY);
  }, []);

  const loadChat = useCallback(async (id: string) => {
    try {
      isUserInitiatedLoad.current = true;
      const res = await fetch(`${API_URL}/chat/history/${id}`);
      if (!res.ok) throw new Error("Failed to load");
      const data = await res.json();
      setMessages(data.messages || []);
      setSessionId(data.sessionId || id);
      // Load cumulative token usage from saved chat data
      const COMPACTION_THRESHOLD = 190000;
      if (data.cumulative_usage) {
        const inputTokens = data.cumulative_usage.input_tokens || 0;
        const actualContext = data.cumulative_usage.actual_context || inputTokens;
        const contextPercent = data.cumulative_usage.context_percent ||
          (actualContext / COMPACTION_THRESHOLD) * 100;

        setTokenUsage({
          input: inputTokens,
          output: data.cumulative_usage.output_tokens || 0,
          total: data.cumulative_usage.total_tokens || 0,
          contextPercent: contextPercent,
          actualContext: actualContext
        });
      } else if (data.messages && data.messages.length > 0) {
        // Estimate tokens for old chats that don't have cumulative_usage
        // Rough estimate: ~4 chars per token (conservative), plus base overhead
        const totalChars = data.messages.reduce((acc: number, msg: { content?: string }) =>
          acc + (msg.content?.length || 0), 0);
        const messageTokens = Math.round(totalChars / 4);
        // Add base overhead (system prompt, tools, memory)
        const estimatedTotal = messageTokens + baseOverhead.current;
        const contextPercent = (estimatedTotal / COMPACTION_THRESHOLD) * 100;
        setTokenUsage({
          input: Math.round(estimatedTotal * 0.6),
          output: Math.round(messageTokens * 0.4),
          total: estimatedTotal,
          contextPercent: contextPercent,
          actualContext: estimatedTotal
        });
      } else {
        // Empty chat - still show base overhead
        const contextPercent = (baseOverhead.current / COMPACTION_THRESHOLD) * 100;
        setTokenUsage({
          input: baseOverhead.current,
          output: 0,
          total: baseOverhead.current,
          contextPercent: contextPercent,
          actualContext: baseOverhead.current
        });
      }
    } catch (e) {
      console.error(e);
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
    startNewChat,
    loadChat,
    sessionId,
    connectionStatus,
    tokenUsage
  };
};
