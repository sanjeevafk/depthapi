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
  });

  it("defaults isPro to true when VITE_DEFAULT_IS_PRO is not set", () => {
    expect(useChatStore.getState().isPro).toBe(true);
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
});
