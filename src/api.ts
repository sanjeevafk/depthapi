import type { HistoryItem, PinnedTopic, QueryRequest, QueryResponse, ExportRequest } from "./types";
import type { ShareCreateRequest, ShareCreateResponse, ShareSnapshot } from "./types/shares";
import { LegacyStreamChunkSchema } from "./lib/sseSchemas";
import type { Session } from "@supabase/supabase-js";
import { getTracePropagationHeaders } from "./lib/monitoring";
import type { ApiError } from "./lib/httpErrors";
import { buildApiError } from "./lib/httpErrors";
import {
  EventStreamContentType,
  fetchEventSource,
  type EventSourceMessage,
} from "@microsoft/fetch-event-source";

const API_URL = import.meta.env.VITE_API_URL || "";
const SUPABASE_CONFIGURED =
  Boolean(import.meta.env.VITE_SUPABASE_URL) &&
  Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY);

import { supabase } from "./lib/supabase";

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  litellm: { status: "ok" | "degraded" | "down"; latency_ms: number };
  rate_limit: { status: "ok" | "degraded" | "down" };
  db: { status: "ok" | "degraded" | "down" };
  chat_enabled?: boolean;
  key_valid?: boolean;
}

const getSupabaseSession = async (): Promise<Session | null> => {
  if (!SUPABASE_CONFIGURED) return null;
  const { data } = await supabase.auth.getSession();
  return data.session;
};

const isAbortError = (err: unknown): boolean => {
  return (
    typeof err === "object" &&
    err !== null &&
    "name" in err &&
    (err as { name?: string }).name === "AbortError"
  );
};

const createRequestId = (): string => {
  const webCrypto = globalThis.crypto;
  if (webCrypto?.randomUUID) {
    return webCrypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (webCrypto?.getRandomValues) {
    webCrypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
};

const normalizeError = (err: unknown): Error => {
  return err instanceof Error ? err : new Error("Unexpected error");
};

async function fetchAPI<T>(
  path: string,
  options?: RequestInit & { responseType?: "json" | "blob" },
): Promise<T> {
  let timeoutFired = false;
  const session = await getSupabaseSession();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (options?.headers) {
    const extraHeaders = new Headers(options.headers);
    extraHeaders.forEach((value, key) => {
      headers[key] = value;
    });
  }

  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  }
  Object.assign(headers, getTracePropagationHeaders());
  headers["x-request-id"] = createRequestId();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    timeoutFired = true;
    controller.abort();
  }, 90000); // 90 seconds
  const externalSignal = options?.signal;
  const abortSignalAny = (
    AbortSignal as unknown as {
      any?: (signals: AbortSignal[]) => AbortSignal;
    }
  ).any;
  const combinedSignal = externalSignal
    ? abortSignalAny
      ? abortSignalAny([controller.signal, externalSignal])
      : controller.signal
    : controller.signal;

  let onExternalAbort: (() => void) | null = null;
  if (externalSignal && !abortSignalAny) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      onExternalAbort = () => controller.abort();
      externalSignal.addEventListener("abort", onExternalAbort, { once: true });
    }
  }

  const cleanup = () => {
    clearTimeout(timeoutId);
    if (externalSignal && onExternalAbort) {
      externalSignal.removeEventListener("abort", onExternalAbort);
    }
  };

  try {
    const res = await fetch(`${API_URL}${path}`, {
      ...options,
      headers,
      signal: combinedSignal,
    });
    cleanup();

    if (!res.ok) {
      throw await buildApiError(res);
    }

    if (options?.responseType === "blob") {
      return (await res.blob()) as unknown as T;
    }
    return await res.json();
  } catch (err) {
    cleanup();
    if (isAbortError(err)) {
      if (timeoutFired) {
        throw new Error("Request timed out. Please try again.");
      }
      throw normalizeError(err);
    }
    throw normalizeError(err);
  }
}

export async function getPinnedTopics(): Promise<PinnedTopic[]> {
  return fetchAPI("/api/pinned");
}

export async function getHealth(): Promise<HealthResponse> {
  return fetchAPI("/api/health");
}
export async function queryTopic(
  req: QueryRequest,
  signal?: AbortSignal,
): Promise<QueryResponse> {
  return fetchAPI("/api/query", {
    method: "POST",
    body: JSON.stringify(req),
    signal,
  });
}

export async function queryTopicStream(
  req: QueryRequest,
  onChunk: (chunk: string) => void,
  onDone: (data: Partial<QueryResponse>) => void,
  onError: (err: Error) => void,
  signal?: AbortSignal,
) {
  const session = await getSupabaseSession();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  }
  Object.assign(headers, getTracePropagationHeaders());
  headers["x-request-id"] = createRequestId();

  let retries = 0;
  const maxRetries = 2;
  const baseDelay = 750;

  const sleep = (ms: number) =>
    new Promise((resolve) => setTimeout(resolve, ms));

  const createCombinedSignal = () => {
    const controller = new AbortController();
    const abortSignalAny = (
      AbortSignal as unknown as {
        any?: (signals: AbortSignal[]) => AbortSignal;
      }
    ).any;
    let combinedSignal = controller.signal;
    let onExternalAbort: (() => void) | null = null;
    if (signal) {
      if (abortSignalAny) {
        combinedSignal = abortSignalAny([controller.signal, signal]);
      } else if (signal.aborted) {
        controller.abort();
      } else {
        onExternalAbort = () => controller.abort();
        signal.addEventListener("abort", onExternalAbort, { once: true });
      }
    }
    const cleanupExternal = () => {
      if (signal && onExternalAbort) {
        signal.removeEventListener("abort", onExternalAbort);
      }
    };
    return { controller, combinedSignal, cleanupExternal };
  };

  const fallbackToNonStream = async (reason: string): Promise<void> => {
    if (signal?.aborted) {
      return;
    }
    try {
      console.warn(
        "Streaming unavailable, falling back to non-stream response:",
        reason,
      );
      const data = await queryTopic(req, signal);
      const preferredLevel = req.levels?.[0];
      const levelKey =
        preferredLevel && data.explanations?.[preferredLevel]
          ? preferredLevel
          : Object.keys(data.explanations || {})[0];
      const fullText = levelKey ? data.explanations[levelKey] : "";
      if (fullText) {
        onChunk(fullText);
      }
      onDone(data);
    } catch (err) {
      onError(normalizeError(err));
    }
  };

  while (true) {
    if (signal?.aborted) {
      return;
    }

    let forceFallbackReason: string | null = null;
    let streamCompleted = false;
    let streamErrored = false;
    let waitRetryAfterMs: number | null = null;

    const { controller, combinedSignal, cleanupExternal } =
      createCombinedSignal();

    const handleMessage = (event: EventSourceMessage) => {
      if (combinedSignal.aborted || streamCompleted || streamErrored) {
        return;
      }

      const data = event.data?.trim();
      if (!data) {
        return;
      }

      if (data === "[DONE]" || event.event === "done") {
        streamCompleted = true;
        controller.abort();
        onDone({});
        return;
      }

      let parsed: unknown;
      try {
        parsed = JSON.parse(data);
      } catch (e) {
        console.warn(
          "Failed to parse SSE chunk:",
          data.substring(0, 100),
          e,
        );
        return;
      }

      if (parsed && typeof parsed === "object") {
        const payload = parsed as { status?: string; retry_after_ms?: number };
        if (
          (payload.status === "waiting" ||
            payload.status === "in_progress") &&
          typeof payload.retry_after_ms === "number"
        ) {
          waitRetryAfterMs = Math.max(250, Math.round(payload.retry_after_ms));
          controller.abort();
          return;
        }
      }

      const validated = LegacyStreamChunkSchema.safeParse(parsed);
      if (!validated.success) {
        console.warn("Skipping invalid SSE chunk:", validated.error);
        return;
      }

      if (validated.data.chunk) {
        onChunk(validated.data.chunk);
      } else if (validated.data.warning) {
        onChunk(`\n\n${validated.data.warning}`);
      } else if (validated.data.error) {
        streamErrored = true;
        controller.abort();
        onError(new Error(validated.data.error));
      }
    };

    try {
      await fetchEventSource(`${API_URL}/api/query/stream`, {
        method: "POST",
        headers,
        body: JSON.stringify(req),
        signal: combinedSignal,
        openWhenHidden: true,
        onopen: async (response: Response) => {
          if (response.status === 202) {
            const body = await response.json().catch(() => null);
            const retryAfter =
              body && typeof body.retry_after_ms === "number"
                ? body.retry_after_ms
                : 1000;
            waitRetryAfterMs = Math.max(250, Math.round(retryAfter));
            controller.abort();
            return;
          }
          if (!response.ok) {
            throw await buildApiError(response);
          }
          const contentType = response.headers.get("content-type");
          if (!contentType?.includes(EventStreamContentType)) {
            forceFallbackReason = `Invalid content-type: ${contentType || "unknown"}`;
            throw new Error(forceFallbackReason);
          }
        },
        onmessage: handleMessage,
        onclose: () => {
          if (
            !streamCompleted &&
            !streamErrored &&
            !combinedSignal.aborted &&
            waitRetryAfterMs === null
          ) {
            throw new Error("Stream closed unexpectedly");
          }
        },
        onerror: (err: unknown) => {
          if (isAbortError(err) || waitRetryAfterMs !== null) {
            return;
          }
          if (forceFallbackReason) {
            throw err;
          }
          const error = normalizeError(err) as ApiError;
          const retryAllowed = error.detail?.retry_allowed !== false;
          if (!retryAllowed) {
            throw error;
          }
          if (retries < maxRetries) {
            retries++;
            const delay =
              Math.min(8000, baseDelay * 2 ** (retries - 1)) +
              Math.random() * 250;
            console.warn(
              `Stream failed, retry ${retries}/${maxRetries} in ${Math.round(delay)}ms:`,
              error.message,
            );
            return delay;
          }
          throw error;
        },
      });
    } catch (err) {
      cleanupExternal();
      if (isAbortError(err)) {
        // fall through to retry loop handling if needed
      } else {
        const error = normalizeError(err) as ApiError;
        const retryAllowed = error.detail?.retry_allowed !== false;
        if (!retryAllowed) {
          onError(error);
          return;
        }
        if (waitRetryAfterMs === null) {
          await fallbackToNonStream(
            forceFallbackReason || error.message || "Stream failed",
          );
          return;
        }
      }
    } finally {
      cleanupExternal();
    }

    if (waitRetryAfterMs !== null && !signal?.aborted) {
      await sleep(waitRetryAfterMs);
      continue;
    }
    break;
  }
}

export async function exportExplanations(req: ExportRequest): Promise<Blob> {
  return fetchAPI("/api/export", {
    method: "POST",
    body: JSON.stringify(req),
    responseType: "blob",
  });
}

export async function getHistory(): Promise<HistoryItem[]> {
  return fetchAPI("/api/history");
}

export async function deleteHistoryItem(id: string): Promise<void> {
  return fetchAPI(`/api/history/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function clearHistory(): Promise<void> {
  return fetchAPI("/api/history", { method: "DELETE" });
}

export async function createShare(req: ShareCreateRequest): Promise<ShareCreateResponse> {
  return fetchAPI("/api/shares", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function fetchShareByToken(
  token: string,
  options?: { password?: string },
): Promise<ShareSnapshot> {
  if (options?.password) {
    return fetchAPI(`/api/shares/${encodeURIComponent(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: options.password }),
    });
  }
  return fetchAPI(`/api/shares/${encodeURIComponent(token)}`);
}
