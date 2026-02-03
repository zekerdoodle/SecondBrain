export type MessageStatus = 'pending' | 'confirmed' | 'processing' | 'complete' | 'failed';

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
}
