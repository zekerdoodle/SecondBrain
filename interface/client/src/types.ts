export interface ContentBlock {
  id: string;
  type: 'thinking' | 'text' | 'tool_use' | 'tool_result';
  content: string;
  status: 'in_progress' | 'complete';
  // Tool fields
  tool_name?: string;
  tool_call_id?: string;
  tool_input?: Record<string, unknown>;
  // Legacy runner/Codex aliases kept for defensive rendering of saved history.
  name?: string;
  input?: unknown;
  is_error?: boolean;
  raw_output?: Record<string, unknown>;
  // Thinking/timing fields
  started_at?: number;
  duration_ms?: number;
}

export interface Agent {
  name: string;
  display_name: string;
  description: string;
  model: string;
  is_default: boolean;
  color: string;
  icon: string;
  chattable: boolean;
}

export type MessageStatus = 'pending' | 'confirmed' | 'processing' | 'complete' | 'failed' | 'injected';

export interface FormField {
  id: string;
  type: 'text' | 'textarea' | 'select' | 'checkbox' | 'number' | 'date';
  label: string;
  required?: boolean;
  placeholder?: string;
  options?: Array<{ label: string; value: string }>;
  defaultValue?: any;
}

export interface FormMessageData {
  formId: string;
  title: string;
  description?: string;
  fields: FormField[];
  prefill?: Record<string, any>;
  status: 'pending' | 'submitted';
  submittedValues?: Record<string, any>;
}

export interface ChatImageRef {
  id: string;
  filename: string;
  url: string;
  type: string;
  originalName: string;
}

export interface ChatReplyReference {
  id: string;
  quote: string;
  preview: string;
  wordCount: number;
}

export type ChatDisplaySegment =
  | { type: 'text'; text: string }
  | { type: 'reply'; reference: ChatReplyReference };

export interface ChatDisplayPayload {
  displayContent: string;
  displaySegments?: ChatDisplaySegment[];
  replyReferences?: ChatReplyReference[];
}

export interface ChatTab {
  sessionId: string;
  title: string;
  agent?: string;
  hasUnread: boolean;
  lastActivity: number;
}

export type ContextualMemoryMode = 'auto' | 'off' | 'manual';

export interface ChatHelperSettings {
  titler: {
    paused: boolean;
  };
  contextual_memory: {
    mode: ContextualMemoryMode;
    manual_query: string;
    last_auto_query: string;
  };
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'notice';
  content: string;
  // Notice-only fields (for slash command results, etc.)
  title?: string;
  command?: string;
  ok?: boolean;
  kind?: string;  // "noop" | "error" | undefined (success)
  icon?: string | null;
  isError?: boolean;
  isStreaming?: boolean;
  // @mention agent messages — present when message is from a mentioned agent
  agent?: string;
  // New fields for message persistence
  status?: MessageStatus;  // Track delivery/processing state
  timestamp?: number;      // When message was created (ms since epoch)
  serverTimestamp?: number; // When server confirmed receipt
  // Inline form data (for forms rendered as messages)
  formData?: FormMessageData;
  // Hidden messages (e.g., ping mode wake-up triggers)
  hidden?: boolean;
  // Mid-stream injected message (sent while Claude was working)
  injected?: boolean;
  // Image attachments
  images?: ChatImageRef[];
  // User-message display metadata. content may differ from agentContent when
  // the agent needs hidden context that should render as clean UI chips.
  displayContent?: string;
  displaySegments?: ChatDisplaySegment[];
  replyReferences?: ChatReplyReference[];
  agentContent?: string;
  // Block-based content (present on streaming/new assistant messages)
  blocks?: ContentBlock[];
  // Emoji reactions: { "👍": ["user", "character"], "🔥": ["character"] }
  reactions?: Record<string, string[]>;
}


// ---------------------------------------------------------------------------
// Agent activity (running invocations + scheduled definitions + execution attempts)
// ---------------------------------------------------------------------------

export interface RunningAgentEntry {
  id: string;
  agent: string;
  kind: string;
  started_at: number;
  task_summary: string | null;
  source_chat_id?: string | null;
  conversation_id?: string | null;
  salon_id?: string | null;
  scheduled_task_id?: string | null;
  scheduled_attempt_id?: string | null;
  caller_agent?: string | null;
}

export interface UpcomingScheduledRun {
  id?: string | null;
  task_id?: string | null;
  type: string;
  agent?: string | null;
  name?: string | null;
  silent: boolean;
  active: boolean;
  schedule?: string | null;
  next_run?: string | null;
  due_now?: boolean;
  last_run?: string | null;
  prompt_summary: string;
  project?: string | string[] | null;
  room_id?: string | null;
  error?: string | null;
}

export interface ScheduledExecutionAttempt {
  schema: string | null;
  task_id: string | null;
  attempt_id: string | null;
  task_type: 'agent' | 'prompt' | null;
  agent: string | null;
  state: 'claimed' | 'running' | 'succeeded' | 'failed' | 'legacy' | 'malformed';
  claimed_at: string | null;
  running_at: string | null;
  terminal_at: string | null;
  updated_at: string | null;
  outer_invocation_id: string | null;
  current_inner_invocation_id: string | null;
  conversation_id: string | null;
  continuation_claim_id: string | null;
  resume_count: number;
  error_class: string | null;
  error_code: string | null;
  receipt_error: string | null;
}

export interface AgentActivityResponse {
  generated_at: string;
  running_agents: {
    entries: RunningAgentEntry[] | null;
    error?: string | null;
    source?: string;
    backend_pid?: number;
  };
  upcoming_scheduled_runs: {
    entries: UpcomingScheduledRun[] | null;
    error?: string | null;
    source?: string;
  };
  scheduled_execution_attempts?: {
    entries: ScheduledExecutionAttempt[] | null;
    error?: string | null;
    source?: string;
  };
}

// ---------------------------------------------------------------------------
// Salons (group chats with the Convener) — see interface/server/salon_manager.py
// ---------------------------------------------------------------------------

export interface ConvenerDecision {
  invoke_agent_in_gc: string[];
  /** "yes" | "" | "no" | <int minutes> — see Convener docs */
  gc_active_or_not: string | number;
  reasoning: string;
  hint_updates?: Record<string, any> | null;
  from_message_id?: string;
  chain_index?: number;
}

export interface SalonMessage {
  id: string;
  from: string;          // participant name ("user", "ash", "patch", etc.)
  content: string;
  created_at: number;    // unix seconds
  convener_decision?: ConvenerDecision;
  /** Optional rich blocks (parity with chat). Present on streamed content. */
  blocks?: ContentBlock[];
}

export interface SalonHint {
  mode?: string;
  topics?: string[];
  set_at?: number;
  [key: string]: any;
}

export interface SalonSummary {
  salon_id: string;
  title: string;
  creator: string;
  participants: string[];
  message_count: number;
  created_at: number;
  last_message_at: number;
  gc_active: boolean;
  gc_recheck_minutes?: number;
  convener_recall_at?: number | null;
  locked?: boolean;
  locked_by?: string | null;
}

export interface SalonFull extends SalonSummary {
  agent_hints?: Record<string, SalonHint>;
  messages: SalonMessage[];
  cumulative_usage?: { input_tokens: number; output_tokens: number; total_tokens: number };
  lock?: { locked_at: number; locked_by: string; lock_id: string } | null;
}

/** Persisted tool call message (role: 'tool_call', hidden: true in server data) */
export interface ToolCallMessage {
  id: string;
  role: 'tool_call';
  hidden: true;
  tool_name: string;
  tool_id: string;
  args: Record<string, any>;
  output_summary?: string;
  is_error?: boolean;
  timestamp?: number;
}
