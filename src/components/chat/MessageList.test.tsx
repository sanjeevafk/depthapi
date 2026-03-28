import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import MessageList from "./MessageList";
import { useConversationStore } from "../../stores/useConversationStore";
import { useMessageStore } from "../../stores/useMessageStore";

const resetStores = () => {
  useMessageStore.setState(useMessageStore.getInitialState(), true);
  useConversationStore.setState(useConversationStore.getInitialState(), true);
};

describe("MessageList rendering", () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      value: vi.fn(),
      writable: true,
    });
  });

  beforeEach(() => {
    resetStores();
  });

  it("renders messages when message store updates", async () => {
    render(<MessageList />);

    expect(
      screen.getByText("Start a conversation to see messages here."),
    ).toBeInTheDocument();

    await act(async () => {
      useMessageStore.setState({
        messageIds: ["u1", "a1"],
        messagesById: {
          u1: {
            id: "u1",
            role: "user",
            content: "What is caching?",
            created_at: "2026-01-01T00:00:00.000Z",
          },
          a1: {
            id: "a1",
            role: "assistant",
            content: "Caching stores reusable data for faster access.",
            created_at: "2026-01-01T00:00:01.000Z",
            metadata: { mode: "learning" },
          },
        },
      });
    });

    expect(screen.getByText("What is caching?")).toBeInTheDocument();
    expect(
      screen.getByText("Caching stores reusable data for faster access."),
    ).toBeInTheDocument();
  });

  it("re-renders when streamed assistant content changes", async () => {
    useMessageStore.setState({
      messageIds: ["a1"],
      messagesById: {
        a1: {
          id: "a1",
          role: "assistant",
          content: "Hello",
          created_at: "2026-01-01T00:00:00.000Z",
          isStreaming: true,
          metadata: { mode: "learning" },
        },
      },
    });

    render(<MessageList />);
    expect(screen.getByText("Hello")).toBeInTheDocument();

    await act(async () => {
      useMessageStore.setState((state) => ({
        messagesById: {
          ...state.messagesById,
          a1: {
            ...state.messagesById.a1!,
            content: "Hello world",
            isStreaming: true,
          },
        },
      }));
    });

    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });
});
