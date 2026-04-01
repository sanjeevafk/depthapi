export type ShareAccessLevel = "public";
export type ShareKind = "response" | "conversation";

export interface ShareCreateRequest {
  message_id?: string;
  conversation_id?: string;
  share_kind?: ShareKind;
  access_level: ShareAccessLevel;
  title?: string | null;
}

export interface ShareCreateResponse {
  share_id: string;
  share_token: string;
  share_url: string;
  access_level: ShareAccessLevel;
  expires_at?: string | null;
}

export interface ShareSnapshot {
  id: string;
  share_token: string;
  title?: string | null;
  prompt_text: string;
  response_text: string;
  metadata: Record<string, unknown>;
  access_level: ShareAccessLevel;
  share_kind: ShareKind;
  snapshot_messages: Array<{
    id?: string;
    role?: string;
    content?: string;
    created_at?: string;
  }>;
  created_at: string;
  expires_at?: string | null;
  view_count: number;
}
