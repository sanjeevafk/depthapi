import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";

const selectConversation = vi.fn();
const addMessage = vi.fn();
const removeChannel = vi.fn().mockResolvedValue(undefined);

vi.mock("../stores/useChatStore", () => ({
  useChatStore: (selector: (state: { selectConversation: typeof selectConversation; addMessage: typeof addMessage }) => unknown) =>
    selector({ selectConversation, addMessage }),
}));

vi.mock("../stores/useConversationStore", () => ({
  useConversationStore: (selector: (state: { currentConversationId: string | null }) => unknown) =>
    selector({ currentConversationId: "conv-1" }),
}));

vi.mock("../lib/supabase", () => ({
  supabase: {
    channel: vi.fn(() => ({
      on: vi.fn().mockReturnThis(),
      subscribe: vi.fn(),
    })),
    removeChannel,
  },
}));

describe("useMessages", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.stubEnv("VITE_SUPABASE_URL", "https://example.supabase.co");
    vi.stubEnv("VITE_SUPABASE_PUBLISHABLE_KEY", "publishable-key");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("loads active conversation without force reload", async () => {
    const { useMessages } = await import("./useMessages");
    function HookHarness(): JSX.Element | null {
      useMessages();
      return null;
    }

    render(<HookHarness />);
    await Promise.resolve();

    expect(selectConversation).toHaveBeenCalledWith("conv-1", {
      forceReload: false,
    });
  });
});
