import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthContext";
import { useChatStore } from "../stores/useChatStore";

const {
  mockGetSession,
  mockOnAuthStateChange,
  mockSignOut,
  mockSignInWithOAuth,
  mockFromSingle,
} = vi.hoisted(() => ({
  mockGetSession: vi.fn(),
  mockOnAuthStateChange: vi.fn(),
  mockSignOut: vi.fn(),
  mockSignInWithOAuth: vi.fn(),
  mockFromSingle: vi.fn(),
}));

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: mockGetSession,
      onAuthStateChange: mockOnAuthStateChange,
      signOut: mockSignOut,
      signInWithOAuth: mockSignInWithOAuth,
    },
    from: vi.fn(() => ({
      select: vi.fn(() => ({
        eq: vi.fn(() => ({
          single: mockFromSingle,
        })),
      })),
    })),
  },
}));

describe("AuthContext", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("VITE_SUPABASE_URL", "https://example.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "test-key");
    useChatStore.setState(
      { ...useChatStore.getInitialState(), isPro: false },
      true,
    );
    mockOnAuthStateChange.mockReturnValue({
      data: {
        subscription: {
          unsubscribe: vi.fn(),
        },
      },
    });
  });

  it("syncs Supabase profile is_pro into the chat store", async () => {
    mockGetSession.mockResolvedValue({
      data: {
        session: {
          user: { id: "user-123" },
        },
      },
      error: null,
    });

    mockFromSingle.mockResolvedValue({
      data: { is_pro: true },
      error: null,
    });

    render(
      <AuthProvider>
        <div>child</div>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(useChatStore.getState().isPro).toBe(true);
    });
  });
});
