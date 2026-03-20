export interface PinnedTopic {
  id: string;
  title: string;
  description: string;
}

export interface QueryRequest {
  topic: string;
  levels?: string[];
  premium?: boolean;
  bypass_cache?: boolean;
  mode?: "learning" | "technical" | "socratic";
  temperature?: number;
  regenerate?: boolean;
}

export type Mode = "learning" | "technical" | "socratic";

export interface QueryResponse {
  topic: string;
  explanations: Record<string, string>;
  cached: boolean;
  mode?: Mode;
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
