import { supabase } from "../lib/supabase";
import { splitSseEvents, parseSseEvent } from "../lib/sse";
import { ChatStreamChunkSchema } from "../lib/sseSchemas";
import { getTracePropagationHeaders } from "../lib/monitoring";
import { toQueryLevel } from "../lib/chatModes";
import type { ChatMode, PromptMode } from "../types/chat";
import { API_URL, createUuid, supabaseConfigured } from "../lib/chatStoreUtils";

interface SendChatParams {
  conversationId: string;
  content: string;
  mode: ChatMode;
  promptMode: PromptMode;
  temperature: number;
  isPro: boolean;
  isRegeneration?: boolean;
  clientMessageId: string;
  assistantClientId: string;
  signal: AbortSignal;
  onChunk: (chunk: string) => void;
  onServerMessageId?: (id: string) => void;
  onError: (error: Error) => void;
  onDone: () => void;
}

const getSupabaseSession = async () => {
  if (!supabaseConfigured) return null;
  const { data } = await supabase.auth.getSession();
  return data.session;
};

const buildHeaders = async (): Promise<Record<string, string>> => {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const session = await getSupabaseSession();
  if (session?.access_token) {
    headers.Authorization = `Bearer ${session.access_token}`;
  }
  Object.assign(headers, getTracePropagationHeaders());
  headers["x-request-id"] = createUuid();
  return headers;
};

const buildHttpError = async (response: Response): Promise<Error & { status?: number }> => {
  let message = "";
  try {
    const payload = (await response.json()) as Record<string, unknown>;
    const detail = payload.detail;
    const error = payload.error;
    if (typeof detail === "string" && detail.trim()) {
      message = detail.trim();
    } else if (typeof error === "string" && error.trim()) {
      message = error.trim();
    }
  } catch {
    // ignore non-json error payloads
  }

  const err = new Error(
    message || `Request failed with status ${response.status}`,
  ) as Error & { status?: number };
  err.status = response.status;
  return err;
};

const handlePayload = (
  rawPayload: unknown,
  chunkKey: "delta" | "chunk",
  onChunk: (chunk: string) => void,
  onServerMessageId?: (id: string) => void,
) => {
  const parsed = ChatStreamChunkSchema.safeParse(rawPayload);
  if (!parsed.success) return;

  const payload = parsed.data;
  const chunk = payload?.[chunkKey] ?? payload?.delta ?? payload?.chunk;
  if (chunk) {
    onChunk(chunk);
  }

  const serverMessageId = payload?.assistant_message_id || payload?.message_id;
  if (serverMessageId && onServerMessageId) {
    onServerMessageId(serverMessageId);
  }

  if (payload?.error) {
    throw new Error(payload.error);
  }
};

async function streamSSE(
  response: Response,
  signal: AbortSignal,
  onPayload: (payload: unknown) => void,
): Promise<void> {
  if (!response.body) throw new Error("Streaming not supported");
  const contentType = response.headers.get("content-type");
  if (contentType && !contentType.includes("text/event-stream")) {
    throw new Error(`Unexpected content-type: ${contentType}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const READ_TIMEOUT_MS = 20_000;
  let doneReceived = false;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;

  const abortHandler = () => {
    clearTimeout(timeoutId);
    reader.cancel().catch(() => {});
  };
  signal.addEventListener("abort", abortHandler, { once: true });

  try {
    while (true) {
      if (signal.aborted) break;

      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await Promise.race([
          reader.read(),
          new Promise<ReadableStreamReadResult<Uint8Array>>((_, reject) => {
            timeoutId = setTimeout(
              () => reject(new Error("Stream read timed out")),
              READ_TIMEOUT_MS,
            );
          }),
        ]);
      } finally {
        clearTimeout(timeoutId);
      }

      const { value, done } = result;
      buffer += done
        ? decoder.decode(undefined, { stream: false })
        : decoder.decode(value, { stream: true });

      const { events, remainder } = splitSseEvents(buffer);
      buffer = remainder;

      for (const eventBlock of events) {
        const parsed = parseSseEvent(eventBlock);
        if (!parsed) continue;
        if (parsed.event === "heartbeat") continue;
        if (parsed.event === "done" || parsed.data === "[DONE]") {
          doneReceived = true;
          break;
        }
        let payload: unknown;
        try {
          payload = JSON.parse(parsed.data);
        } catch {
          payload = { delta: parsed.data };
        }
        onPayload(payload);
      }

      if (done || doneReceived) break;
    }
  } finally {
    signal.removeEventListener("abort", abortHandler);
    reader.cancel().catch(() => {});
  }
}

export async function sendChat(params: SendChatParams): Promise<void> {
  try {
    const headers = await buildHeaders();
    const session = await getSupabaseSession();

    const fallbackToQueryStream = async () => {
      const fallbackLevel = toQueryLevel(params.promptMode);
      const fallbackResponse = await fetch(`${API_URL}/api/query/stream`, {
        method: "POST",
        headers,
        signal: params.signal,
        body: JSON.stringify({
          topic: params.content,
          levels: [fallbackLevel],
          mode: params.mode,
          premium: params.isPro,
          regenerate: Boolean(params.isRegeneration),
          bypass_cache: Boolean(params.isRegeneration),
          temperature: params.temperature,
          message_id: params.clientMessageId,
        }),
      });

      if (!fallbackResponse.ok) {
        throw await buildHttpError(fallbackResponse);
      }

      await streamSSE(fallbackResponse, params.signal, (payload) =>
        handlePayload(
          payload,
          "chunk",
          params.onChunk,
          params.onServerMessageId,
        ),
      );
    };

    const shouldUseMessagesEndpoint =
      Boolean(session?.access_token) &&
      supabaseConfigured &&
      !params.conversationId.startsWith("local-");

    if (!shouldUseMessagesEndpoint) {
      await fallbackToQueryStream();
      params.onDone();
      return;
    }

    const response = await fetch(`${API_URL}/api/messages`, {
      method: "POST",
      headers,
      signal: params.signal,
      body: JSON.stringify({
        conversation_id: params.conversationId,
        content: params.content,
        client_generated_id: params.clientMessageId,
        assistant_client_id: params.assistantClientId,
        mode: params.mode,
        prompt_mode: params.promptMode,
        regenerate: Boolean(params.isRegeneration),
        temperature: params.temperature,
      }),
    });

    if (response.status === 404 || response.status === 405) {
      await fallbackToQueryStream();
      params.onDone();
      return;
    }

    if (!response.ok) {
      throw await buildHttpError(response);
    }

    await streamSSE(response, params.signal, (payload) =>
      handlePayload(
        payload,
        "delta",
        params.onChunk,
        params.onServerMessageId,
      ),
    );

    params.onDone();
  } catch (error) {
    params.onError(
      error instanceof Error ? error : new Error(String(error)),
    );
  }
}
