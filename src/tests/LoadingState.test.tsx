import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { LoadingState } from "../components/LoadingState";
import {
  FALLBACK_CACHE_KEY,
  FALLBACK_QUOTES,
} from "../components/loadingStateConstants";

describe("LoadingState", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renders a fetched quote when the API succeeds", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ content: "Test quote", author: "Ada" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<LoadingState mode="learning" level="eli5" topic="math" />);

    expect(await screen.findByText(/«Test quote» — Ada/i)).toBeInTheDocument();
  });

  it("falls back to a cached quote when the API fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("fail")));
    vi.spyOn(Math, "random").mockReturnValue(0);

    render(<LoadingState mode="learning" level="eli10" topic="history" />);

    const fallbackQuote = FALLBACK_QUOTES[0];
    expect(await screen.findByText(fallbackQuote)).toBeInTheDocument();

    const cached = window.localStorage.getItem(FALLBACK_CACHE_KEY);
    expect(cached).toBeTruthy();
    expect(JSON.parse(cached || "{}").quote).toBe(fallbackQuote);
  });
});
