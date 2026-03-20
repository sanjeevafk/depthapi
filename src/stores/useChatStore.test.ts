import { beforeEach, describe, expect, it, vi } from "vitest";

type MockBuilder = {
  select: () => MockBuilder;
  insert: () => MockBuilder;
  update: () => MockBuilder;
  delete: () => MockBuilder;
  eq: () => MockBuilder;
  in: () => MockBuilder;
  single: () => Promise<{ data: null; error: null }>;
  order: () => Promise<{ data: unknown[]; error: null }>;
  execute: () => Promise<{ data: unknown[]; error: null }>;
};

const createBuilder = () => {
  const builder = {} as MockBuilder;
  builder.select = vi.fn(() => builder);
  builder.insert = vi.fn(() => builder);
  builder.update = vi.fn(() => builder);
  builder.delete = vi.fn(() => builder);
  builder.eq = vi.fn(() => builder);
  builder.in = vi.fn(() => builder);
  builder.single = vi.fn(async () => ({ data: null, error: null }));
  builder.order = vi.fn(async () => ({ data: [], error: null }));
  builder.execute = vi.fn(async () => ({ data: [], error: null }));
  return builder;
};

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(async () => ({ data: { session: null } })),
      getUser: vi.fn(async () => ({ data: { user: null } })),
    },
    from: vi.fn(() => createBuilder()),
    channel: vi.fn(() => ({
      on: vi.fn().mockReturnThis(),
      subscribe: vi.fn(),
    })),
    removeChannel: vi.fn(),
  },
}));

import { useChatStore } from "./useChatStore";

const initialState = useChatStore.getInitialState();

describe("useChatStore", () => {
  beforeEach(() => {
    useChatStore.setState(initialState, true);
    vi.restoreAllMocks();
  });

  it("defaults isPro to false when VITE_DEFAULT_IS_PRO is not set", () => {
    expect(useChatStore.getState().isPro).toBe(false);
  });

  it("aborts active streams when starting a new thread", () => {
    const controller = new AbortController();

    useChatStore.setState({
      currentConversationId: "local-conv-1",
      messageIds: ["assistant-1"],
      messagesById: {
        "assistant-1": {
          id: "assistant-1",
          role: "assistant",
          content: "streaming",
          created_at: "2026-01-01T00:00:00.000Z",
          clientGeneratedId: "assistant-client-1",
          isStreaming: true,
        },
      },
      streamControllers: { "assistant-client-1": controller },
    });

    useChatStore.getState().startNewThread();

    const state = useChatStore.getState();
    expect(controller.signal.aborted).toBe(true);
    expect(state.currentConversationId).toBeNull();
    expect(state.isDraftThread).toBe(true);
    expect(state.messageIds).toEqual([]);
    expect(state.streamControllers).toEqual({});
  });

  it("deletes active conversation and switches to newest remaining conversation", async () => {
    useChatStore.setState({
      conversations: [
        {
          id: "local-old",
          title: "Old",
          mode: "learning",
          settings: { mode: "learning" },
          created_at: "2026-01-01T00:00:00.000Z",
          updated_at: "2026-01-01T00:00:00.000Z",
        },
        {
          id: "local-new",
          title: "New",
          mode: "socratic",
          settings: { mode: "socratic" },
          created_at: "2026-01-01T00:00:00.000Z",
          updated_at: "2026-01-02T00:00:00.000Z",
        },
      ],
      currentConversationId: "local-old",
    });

    await useChatStore.getState().deleteConversation("local-old");

    const state = useChatStore.getState();
    expect(state.conversations).toHaveLength(1);
    expect(state.conversations[0].id).toBe("local-new");
    expect(state.currentConversationId).toBe("local-new");
  });

  it("deletes the last active conversation and starts a new draft thread", async () => {
    useChatStore.setState({
      conversations: [
        {
          id: "local-only",
          title: "Only conversation",
          mode: "learning",
          settings: { mode: "learning" },
          created_at: "2026-01-01T00:00:00.000Z",
          updated_at: "2026-01-02T00:00:00.000Z",
        },
      ],
      currentConversationId: "local-only",
    });

    await useChatStore.getState().deleteConversation("local-only");

    const state = useChatStore.getState();
    expect(state.conversations).toEqual([]);
    expect(state.currentConversationId).toBeNull();
    expect(state.isDraftThread).toBe(true);
  });

  it("regenerates from original prompt and replaces assistant response", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          [
            'id: 1\nevent: meta\ndata: {"assistant_message_id":"assistant-server"}\n\n',
            'id: 2\nevent: delta\ndata: {"delta":"Regenerated answer"}\n\n',
            "id: 3\nevent: done\ndata: [DONE]\n\n",
          ].join(""),
          { headers: { "content-type": "text/event-stream" } },
        ),
      );

    useChatStore.setState({
      currentConversationId: "local-conv-1",
      conversations: [
        {
          id: "local-conv-1",
          title: "Thread",
          mode: "learning",
          settings: { mode: "learning", prompt_mode: "eli12" },
          created_at: "2026-01-01T00:00:00.000Z",
          updated_at: "2026-01-01T00:00:00.000Z",
        },
      ],
      messageIds: ["user-1", "assistant-1"],
      messagesById: {
        "user-1": {
          id: "user-1",
          role: "user",
          content: "Explain gravity simply",
          created_at: "2026-01-01T00:00:00.000Z",
          metadata: { client_id: "user-client-1", mode: "learning", prompt_mode: "eli12" },
        },
        "assistant-1": {
          id: "assistant-1",
          role: "assistant",
          content: "Old assistant answer",
          created_at: "2026-01-01T00:00:00.000Z",
          clientGeneratedId: "assistant-client-old",
          metadata: {
            mode: "learning",
            prompt_mode: "eli12",
            temperature: 0.7,
            assistant_client_id: "assistant-client-old",
          },
        },
      },
    });

    await useChatStore.getState().regenerateMessage("assistant-1");

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const fetchInit = fetchSpy.mock.calls[0][1] as RequestInit;
    const payload = JSON.parse(String(fetchInit.body));
    const requestedText = payload.content ?? payload.topic;
    expect(requestedText).toBe("Explain gravity simply");
    expect(payload.mode).toBe("learning");
    const promptMode = payload.prompt_mode ?? payload.promptMode;
    if (promptMode) {
      expect(promptMode).toBe("eli12");
    } else {
      expect(payload.levels).toEqual(["eli12"]);
    }
    expect(payload.regenerate).toBe(true);
    expect(payload.temperature).toBeCloseTo(0.8);

    const state = useChatStore.getState();
    expect(state.messageIds).toEqual(["user-1", "assistant-1"]);
    expect(state.messagesById["assistant-1"].content).toBe("Regenerated answer");
    expect(state.messagesById["assistant-1"].isRegenerating).toBe(false);
  });

  it("clamps regeneration temperature to 1.0", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          "id: 1\nevent: done\ndata: [DONE]\n\n",
          { headers: { "content-type": "text/event-stream" } },
        ),
      );

    useChatStore.setState({
      currentConversationId: "local-conv-2",
      conversations: [
        {
          id: "local-conv-2",
          title: "Thread",
          mode: "socratic",
          settings: { mode: "socratic", prompt_mode: "eli15" },
          created_at: "2026-01-01T00:00:00.000Z",
          updated_at: "2026-01-01T00:00:00.000Z",
        },
      ],
      messageIds: ["user-2", "assistant-2"],
      messagesById: {
        "user-2": {
          id: "user-2",
          role: "user",
          content: "Why do stars shine?",
          created_at: "2026-01-01T00:00:00.000Z",
          metadata: { client_id: "user-client-2" },
        },
        "assistant-2": {
          id: "assistant-2",
          role: "assistant",
          content: "old",
          created_at: "2026-01-01T00:00:00.000Z",
          metadata: {
            mode: "socratic",
            prompt_mode: "eli15",
            temperature: 0.95,
            assistant_client_id: "assistant-client-2",
          },
        },
      },
    });

    await useChatStore.getState().regenerateMessage("assistant-2");

    const fetchInit = fetchSpy.mock.calls[0][1] as RequestInit;
    const payload = JSON.parse(String(fetchInit.body));
    expect(payload.temperature).toBe(1);
  });

  it("blocks concurrent regeneration requests", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    useChatStore.setState({
      regeneratingMessageId: "assistant-locked",
      messageIds: ["user-3", "assistant-locked"],
      messagesById: {
        "user-3": {
          id: "user-3",
          role: "user",
          content: "prompt",
          created_at: "2026-01-01T00:00:00.000Z",
        },
        "assistant-locked": {
          id: "assistant-locked",
          role: "assistant",
          content: "answer",
          created_at: "2026-01-01T00:00:00.000Z",
        },
      },
    });

    await useChatStore.getState().regenerateMessage("assistant-locked");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
