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
  role: 'user' | 'assistant' | 'system';
  content: string;
  isError?: boolean;
  isStreaming?: boolean;
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
