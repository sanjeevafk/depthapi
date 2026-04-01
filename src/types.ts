export interface PinnedTopic {
  id: string;
  title: string;
  description: string;
}

export interface QueryRequest {
  topic: string;  // Max 100,000 chars; truncated by backend per mode
  levels?: string[];
  premium?: boolean;
  bypass_cache?: boolean;
  mode?: "learn" | "technical" | "socratic";
  temperature?: number;
  regenerate?: boolean;
}

export type Mode = "learn" | "technical" | "socratic";

export interface TruncationInfo {
  was_truncated: boolean;
  original_length: number;
  truncated_to: number;
  truncation_reason: string | null;
}

export interface QueryResponse {
  topic: string;
  explanations: Record<string, string>;
  cached: boolean;
  mode?: Mode;
  truncation_info?: TruncationInfo;
}

export interface HistoryItem {
  id: string;
  topic: string;
  levels: string[];
  mode: Mode;
  created_at: string;
}

export interface ExportRequest {
  topic: string;
  explanations: Record<string, string>;
  format: "txt" | "md";
  premium?: boolean;
  mode?: Mode;
  visuals?: Record<string, string>;
}

export const FREE_LEVELS = ["eli5", "eli10", "eli12", "eli15", "meme"] as const;
export const PREMIUM_LEVELS = [] as const;
export type Level = (typeof FREE_LEVELS)[number];
