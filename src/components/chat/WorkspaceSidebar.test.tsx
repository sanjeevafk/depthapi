import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import WorkspaceSidebar from "./WorkspaceSidebar";
import type { Conversation } from "../../types/chat";

const conversations: Conversation[] = [
  {
    id: "conv-1",
    title: "Democracy",
    mode: "socratic",
    settings: { mode: "socratic" },
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-02T00:00:00.000Z",
  },
  {
    id: "conv-2",
    title: "Deep learning",
    mode: "technical",
    settings: { mode: "technical" },
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-03T00:00:00.000Z",
  },
];

describe("WorkspaceSidebar", () => {
  const onClose = vi.fn();
  const onNewThread = vi.fn();
  const onWorkspaceChange = vi.fn();
  const onSelectConversation = vi.fn();
  const onDeleteConversation = vi.fn();
  const onToggleCollapse = vi.fn();

  beforeEach(() => {
    onClose.mockReset();
    onNewThread.mockReset();
    onWorkspaceChange.mockReset();
    onSelectConversation.mockReset();
    onDeleteConversation.mockReset();
    onToggleCollapse.mockReset();
  });

  it("always starts a new thread when workspace button is clicked", () => {
    render(
      <WorkspaceSidebar
        workspace="learn"
        conversations={conversations}
        currentConversationId="conv-1"
        isOpen
        isCollapsed={false}
        userName="Tester"
        onClose={onClose}
        onToggleCollapse={onToggleCollapse}
        onNewThread={onNewThread}
        onWorkspaceChange={onWorkspaceChange}
        onSelectConversation={onSelectConversation}
        onDeleteConversation={onDeleteConversation}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Socratic" }));

    expect(onNewThread).toHaveBeenCalledTimes(1);
    expect(onWorkspaceChange).toHaveBeenCalledWith("socratic");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("deletes a conversation only after confirmation", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <WorkspaceSidebar
        workspace="learn"
        conversations={conversations}
        currentConversationId="conv-1"
        isOpen
        isCollapsed={false}
        userName="Tester"
        onClose={onClose}
        onToggleCollapse={onToggleCollapse}
        onNewThread={onNewThread}
        onWorkspaceChange={onWorkspaceChange}
        onSelectConversation={onSelectConversation}
        onDeleteConversation={onDeleteConversation}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete Democracy" }));

    expect(confirmSpy).toHaveBeenCalledWith(
      "Delete this chat? This cannot be undone.",
    );
    expect(onDeleteConversation).toHaveBeenCalledWith("conv-1");

    confirmSpy.mockRestore();
  });

  it("does not delete a conversation when confirmation is cancelled", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(
      <WorkspaceSidebar
        workspace="learn"
        conversations={conversations}
        currentConversationId="conv-1"
        isOpen
        isCollapsed={false}
        userName="Tester"
        onClose={onClose}
        onToggleCollapse={onToggleCollapse}
        onNewThread={onNewThread}
        onWorkspaceChange={onWorkspaceChange}
        onSelectConversation={onSelectConversation}
        onDeleteConversation={onDeleteConversation}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete Democracy" }));

    expect(onDeleteConversation).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });
});
