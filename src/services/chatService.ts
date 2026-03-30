import { supabase } from "../lib/supabase";
import { splitSseEvents, parseSseEvent } from "../lib/sse";
import { ChatStreamChunkSchema } from "../lib/sseSchemas";
import { getTracePropagationHeaders } from "../lib/monitoring";
import { toQueryLevel } from "../lib/chatModes";
import type { ChatMode, PromptMode } from "../types/chat";
import { API_URL, createUuid, supabaseConfigured } from "../lib/chatStoreUtils";
import { buildApiError } from "../lib/httpErrors";

interface SendChatParams {
  conversationId: string;
  content: string;
  mode: ChatMode;
  promptMode: PromptMode;
  temperature: number;
  isPro: boolean;
  history?: Array<{ role: "user" | "assistant" | "system"; content: string }>;
  isRegeneration?: boolean;
  clientMessageId: string;
  assistantClientId: string;
  signal: AbortSignal;
  onChunk: (chunk: string) => void;
  onServerMessageId?: (id: string) => void;
  onError: (error: Error) => void;
  onDone: () => void;
}

class StreamWaitError extends Error {
  retryAfterMs: number;

  constructor(retryAfterMs: number) {
    super("Request already in progress");
    this.name = "StreamWaitError";
    this.retryAfterMs = retryAfterMs;
  }
}

const createAbortError = (): Error => {
  const error = new Error("Aborted");
  (error as Error & { name: string }).name = "AbortError";
  return error;
};

const waitFor = (ms: number, signal: AbortSignal): Promise<void> => {
  if (signal.aborted) {
    return Promise.reject(createAbortError());
  }
  return new Promise((resolve, reject) => {
    const timeout = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);

    const onAbort = () => {
      globalThis.clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      reject(createAbortError());
    };

    signal.addEventListener("abort", onAbort, { once: true });
  });
};

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

const buildHttpError = async (response: Response) => {
  return buildApiError(response);
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
    const fragments = chunk.match(/\S+\s*|\s+/g) ?? [chunk];
    for (const fragment of fragments) {
      onChunk(fragment);
    }
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

    if (signal.aborted && !doneReceived) {
      throw createAbortError();
    }

    if (!doneReceived) {
      throw new Error("Stream closed unexpectedly");
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
      const maxWaitRetries = 4;

      for (let attempt = 0; attempt <= maxWaitRetries; attempt++) {
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
            history: params.history,
          }),
        });

        if (!fallbackResponse.ok) {
          throw await buildHttpError(fallbackResponse);
        }

        try {
          await streamSSE(fallbackResponse, params.signal, (payload) => {
            if (payload && typeof payload === "object") {
              const statusPayload = payload as {
                status?: string;
                retry_after_ms?: number;
              };
              if (
                (statusPayload.status === "waiting" ||
                  statusPayload.status === "in_progress") &&
                typeof statusPayload.retry_after_ms === "number"
              ) {
                throw new StreamWaitError(
                  Math.max(250, Math.round(statusPayload.retry_after_ms)),
                );
              }
            }

            handlePayload(
              payload,
              "chunk",
              params.onChunk,
              params.onServerMessageId,
            );
          });
          return;
        } catch (error) {
          if (!(error instanceof StreamWaitError)) {
            throw error;
          }
          if (attempt >= maxWaitRetries) {
            throw new Error(
              "The previous request is still processing. Please retry in a moment.",
            );
          }
          await waitFor(error.retryAfterMs, params.signal);
        }
      }
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
