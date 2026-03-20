import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChatStore } from "../stores/useChatStore";

type FetchArgs = Parameters<typeof fetch>;

const resetStore = () => {
  const state = useChatStore.getState();
  useChatStore.setState(
    {
      ...state,
      conversations: [],
      currentConversationId: null,
      isDraftThread: false,
      messagesById: {},
      messageIds: [],
      streamControllers: {},
      isLoading: false,
      regeneratingMessageId: null,
      regenerationModalOpen: false,
      regenerationTargetId: null,
    },
    true,
  );
};

const seedConversation = () => {
  const now = new Date().toISOString();
  useChatStore.setState({
    conversations: [
      {
        id: "local-test",
        title: "Test",
        mode: "learning",
        settings: { mode: "learning", prompt_mode: "eli5" },
        created_at: now,
        updated_at: now,
      },
    ],
    currentConversationId: "local-test",
  });
};

const makeSseResponse = (chunks: string[]) => {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "content-type": "text/event-stream" },
    status: 200,
  });
};

const makeErrorResponse = (
  status: number,
  payload: Record<string, unknown>,
) => {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
};

const findAssistantMessage = () => {
  const { messageIds, messagesById } = useChatStore.getState();
  return messageIds
    .map((id) => messagesById[id])
    .find((message) => message?.role === "assistant");
};

describe("useChatStore streaming", () => {
  beforeEach(() => {
    resetStore();
    seedConversation();
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("streams a successful response", async () => {
    const sse = [
      'id: 1\nevent: meta\ndata: {"assistant_message_id":"assist-1"}\n\n',
      'id: 2\nevent: delta\ndata: {"delta":"Hello "}\n\n',
      'id: 3\nevent: delta\ndata: {"delta":"World"}\n\n',
      "id: 4\nevent: done\ndata: [DONE]\n\n",
    ];
    const fetchMock = vi
      .fn<(...args: FetchArgs) => Promise<Response>>()
      .mockResolvedValue(makeSseResponse(sse));
    vi.stubGlobal("fetch", fetchMock);

    await useChatStore.getState().sendMessage("Hello world");

    const assistant = findAssistantMessage();
    expect(assistant?.content).toBe("Hello World");
    expect(assistant?.syncStatus).toBe("synced");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("skips invalid SSE payloads without crashing", async () => {
    const sse = [
      'event: delta\ndata: {"delta":"skip"}\n\n',
      "id: 1\nevent: delta\ndata: 123\n\n",
      'id: 2\nevent: delta\ndata: {"delta":"Good"}\n\n',
      "id: 3\nevent: done\ndata: [DONE]\n\n",
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeSseResponse(sse)));

    await useChatStore.getState().sendMessage("Test");

    const assistant = findAssistantMessage();
    expect(assistant?.content).toBe("Good");
  });

  it("marks stream timeout as retryable error", async () => {
    vi.useFakeTimers();
    const stream = new ReadableStream<Uint8Array>({
      start() {
        // Intentionally never enqueue to trigger read timeout.
      },
    });
    const response = new Response(stream, {
      headers: { "content-type": "text/event-stream" },
      status: 200,
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    const sendPromise = useChatStore.getState().sendMessage("Timeout");
    await vi.advanceTimersByTimeAsync(20_000);
    await sendPromise;

    const assistant = findAssistantMessage();
    expect(assistant?.error).toBe("Streaming timed out. Retry.");
    expect(assistant?.syncStatus).toBe("failed");
  });

  it("handles aborts without treating them as failures", async () => {
    const fetchMock = vi.fn((_: string, init?: RequestInit) => {
      const signal = init?.signal;
      return new Promise<Response>((_resolve, reject) => {
        if (signal?.aborted) {
          const err = new Error("AbortError");
          (err as Error & { name?: string }).name = "AbortError";
          reject(err);
          return;
        }
        signal?.addEventListener("abort", () => {
          const err = new Error("AbortError");
          (err as Error & { name?: string }).name = "AbortError";
          reject(err);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const sendPromise = useChatStore.getState().sendMessage("Abort me");
    await Promise.resolve();
    useChatStore.getState().abortAllStreams();
    await sendPromise;

    const assistant = findAssistantMessage();
    expect(assistant?.error).toBe("Canceled");
    expect(assistant?.isStreaming).toBe(false);
  });

  it("retries failed sync with stored payload", async () => {
    const sse = [
      'id: 1\nevent: delta\ndata: {"delta":"Retry ok"}\n\n',
      "id: 2\nevent: done\ndata: [DONE]\n\n",
    ];
    const fetchMock = vi
      .fn<(...args: FetchArgs) => Promise<Response>>()
      .mockResolvedValueOnce(makeErrorResponse(500, { detail: "Server error" }))
      .mockResolvedValueOnce(makeSseResponse(sse));
    vi.stubGlobal("fetch", fetchMock);

    await useChatStore.getState().sendMessage("Needs retry");

    const assistant = findAssistantMessage();
    expect(assistant?.syncStatus).toBe("failed");
    expect(assistant?.retryPayload).toBeTruthy();

    await useChatStore.getState().retrySync(assistant?.clientGeneratedId || "");

    const updated = findAssistantMessage();
    expect(updated?.content).toBe("Retry ok");
    expect(updated?.syncStatus).toBe("synced");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("useChatStore regeneration", () => {
  beforeEach(() => {
    resetStore();
    seedConversation();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("adds temperature delta and replaces the assistant response", async () => {
    const now = new Date().toISOString();
    useChatStore.setState({
      messagesById: {
        user1: {
          id: "user1",
          role: "user",
          content: "Original prompt",
          created_at: now,
          clientGeneratedId: "user-client-1",
          metadata: { client_id: "user-client-1" },
        },
        assistant1: {
          id: "assistant1",
          role: "assistant",
          content: "Old response",
          created_at: now,
          clientGeneratedId: "assistant-client-1",
          metadata: { temperature: 0.6, mode: "learning", prompt_mode: "eli5" },
        },
      },
      messageIds: ["user1", "assistant1"],
    });

    const sse = [
      'id: 1\nevent: delta\ndata: {"delta":"New answer"}\n\n',
      "id: 2\nevent: done\ndata: [DONE]\n\n",
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeSseResponse(sse)));

    await useChatStore.getState().regenerateMessage("assistant1");

    const assistant = useChatStore.getState().messagesById["assistant1"];
    expect(assistant?.content).toBe("New answer");
    expect(assistant?.metadata?.temperature).toBe(0.7);
  });
});
