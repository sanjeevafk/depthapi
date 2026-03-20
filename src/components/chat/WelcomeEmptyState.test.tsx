import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import WelcomeEmptyState from "./WelcomeEmptyState";
import { WORKSPACE_PROMPTS } from "./welcomeEmptyStateConstants";

describe("WelcomeEmptyState", () => {
  it("renders prompt tiles and triggers selection", () => {
    const onPromptSelect = vi.fn();
    render(
      <WelcomeEmptyState
        workspace="learn"
        userName="Alex"
        onPromptSelect={onPromptSelect}
      />,
    );

    const prompt = WORKSPACE_PROMPTS.learn[0];
    const button = screen.getByRole("button", {
      name: new RegExp(prompt.title, "i"),
    });

    fireEvent.click(button);

    expect(onPromptSelect).toHaveBeenCalledWith(prompt.prompt);
  });
});
