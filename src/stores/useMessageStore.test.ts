import { beforeEach, describe, expect, it } from "vitest";
import { useMessageStore } from "./useMessageStore";

describe("useMessageStore merge behavior", () => {
  beforeEach(() => {
    useMessageStore.setState(useMessageStore.getInitialState(), true);
  });

  it("keeps existing assistant content when duplicate server placeholder is empty", () => {
    useMessageStore.getState().addMessage({
      id: "local-assistant",
      role: "assistant",
      content: "Streamed answer",
      created_at: "2026-03-28T00:00:00.000Z",
      clientGeneratedId: "assistant-client-1",
      metadata: { assistant_client_id: "assistant-client-1", mode: "socratic" },
    });

    useMessageStore.getState().addMessage({
      id: "server-assistant",
      role: "assistant",
      content: "",
      created_at: "2026-03-28T00:00:01.000Z",
      metadata: { assistant_client_id: "assistant-client-1", mode: "socratic" },
    });

    const state = useMessageStore.getState();
    expect(state.messageIds).toHaveLength(1);
    expect(state.messagesById[state.messageIds[0]]?.content).toBe("Streamed answer");
  });

  it("applies duplicate server update when non-empty assistant content arrives", () => {
    useMessageStore.getState().addMessage({
      id: "local-assistant",
      role: "assistant",
      content: "Partial",
      created_at: "2026-03-28T00:00:00.000Z",
      clientGeneratedId: "assistant-client-2",
      metadata: { assistant_client_id: "assistant-client-2", mode: "learning" },
    });

    useMessageStore.getState().addMessage({
      id: "server-assistant",
      role: "assistant",
      content: "Partial + final",
      created_at: "2026-03-28T00:00:01.000Z",
      metadata: { assistant_client_id: "assistant-client-2", mode: "learning" },
    });

    const state = useMessageStore.getState();
    expect(state.messageIds).toHaveLength(1);
    expect(state.messagesById[state.messageIds[0]]?.content).toBe("Partial + final");
  });
});

