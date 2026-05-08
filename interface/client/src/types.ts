export interface ContentBlock {
  id: string;
  type: 'thinking' | 'text' | 'tool_use' | 'tool_result';
  content: string;
  status: 'in_progress' | 'complete';
  // Tool fields
  tool_name?: string;
  tool_call_id?: string;
  tool_input?: Record<string, unknown>;
  is_error?: boolean;
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

export interface ChatTab {
  sessionId: string;
  title: string;
  agent?: string;
  hasUnread: boolean;
  lastActivity: number;
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
  // Block-based content (present on streaming/new assistant messages)
  blocks?: ContentBlock[];
  // Emoji reactions: { "👍": ["user", "character"], "🔥": ["character"] }
  reactions?: Record<string, string[]>;
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
