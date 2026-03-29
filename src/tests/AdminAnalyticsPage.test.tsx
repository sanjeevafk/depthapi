import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const mockUseAuth = vi.fn();

vi.mock("../context/AuthContext", () => ({
  useAuth: () => mockUseAuth(),
}));

const renderPage = async () => {
  const { default: AdminAnalyticsPage } = await import(
    "../pages/AdminAnalyticsPage"
  );
  const queryClient = new QueryClient();
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AdminAnalyticsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
};

describe("AdminAnalyticsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders dashboard for admin users", async () => {
    mockUseAuth.mockReturnValue({
      user: { app_metadata: { role: "admin" } },
      session: { access_token: "token" },
      loading: false,
    });

    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/errors")) {
        return {
          ok: true,
          json: async () => ({ items: [], top_errors: [] }),
        } as Response;
      }
      if (url.includes("/sentry/issues")) {
        return {
          ok: true,
          json: async () => ({ issues: [] }),
        } as Response;
      }
      if (url.includes("/usage")) {
        return {
          ok: true,
          json: async () => ({ items: [], total: 0 }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({ items: [] }),
      } as Response;
    });

    await renderPage();
    expect(
      await screen.findByText(/Analytics Dashboard/i),
    ).toBeInTheDocument();
  });

  it("redirects non-admin users", async () => {
    mockUseAuth.mockReturnValue({
      user: { app_metadata: { role: "user" } },
      session: { access_token: "token" },
      loading: false,
    });

    await renderPage();
    expect(await screen.findByText(/Redirecting/i)).toBeInTheDocument();
  });
});
