export const CHAT_MODES = [
  "eli5",
  "eli10",
  "eli12",
  "eli15",
  "meme",
  "learning",
  "technical",
  "socratic",
] as const;

export const CHAT_FREE_MODES = [
  "eli5",
  "eli10",
  "eli12",
  "eli15",
  "meme",
  "learning",
  "socratic",
] as const;

export const CHAT_PREMIUM_MODES = ["technical"] as const;

export const CHAT_PROMPT_MODES = [
  "eli5",
  "eli10",
  "eli12",
  "eli15",
  "meme",
] as const;

export const LEGACY_CHAT_MODES = [
  "fast",
  "ensemble",
  "default",
  "balanced",
  "technical-depth",
  "technical_depth",
  "meme-style",
] as const;

export type ChatMode = (typeof CHAT_MODES)[number];
export type PromptMode = (typeof CHAT_PROMPT_MODES)[number];
export type LegacyChatMode = (typeof LEGACY_CHAT_MODES)[number];
export type ConversationMode = ChatMode | LegacyChatMode;

export interface Conversation {
  id: string;
  title: string;
  mode: ConversationMode;
  settings: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface MessageMetadata {
  client_id?: string;
  assistant_client_id?: string;
  mode?: ConversationMode;
  prompt_mode?: PromptMode;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: unknown[];
  metadata?: MessageMetadata;
  created_at: string;
  clientGeneratedId?: string;
  serverMessageId?: string;
  isStreaming?: boolean;
  isRegenerating?: boolean;
  error?: string;
  syncStatus?: "pending" | "failed" | "synced";
  retryPayload?: {
    content: string;
    mode: ConversationMode;
    promptMode?: PromptMode;
    temperature?: number;
    clientMessageId?: string;
    assistantClientId?: string;
  };
}
