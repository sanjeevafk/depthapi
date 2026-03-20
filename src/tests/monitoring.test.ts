import { beforeEach, describe, expect, it, vi } from "vitest";

const sentryMocks = vi.hoisted(() => {
  const init = vi.fn();
  const captureMessage = vi.fn();
  const captureException = vi.fn();
  const browserTracingIntegration = vi.fn(() => ({ name: "browserTracingIntegration" }));
  const getTraceData = vi.fn(() => ({
    "sentry-trace": "abc123-def456",
    baggage: "sentry-release=test",
  }));
  const setUser = vi.fn();
  const setTag = vi.fn();
  const setContext = vi.fn();
  const withScope = vi.fn((callback: (scope: {
    setExtra: (key: string, value: unknown) => void;
    setTag: (key: string, value: string) => void;
    setLevel: (level: string) => void;
  }) => void) => {
    const extras: Record<string, unknown> = {};
    const tags: Record<string, string> = {};
    let level = "";

    callback({
      setExtra: (key, value) => {
        extras[key] = value;
      },
      setTag: (key, value) => {
        tags[key] = value;
      },
      setLevel: (nextLevel) => {
        level = nextLevel;
      },
    });

    return { extras, tags, level };
  });

  return {
    init,
    captureMessage,
    captureException,
    withScope,
    browserTracingIntegration,
    getTraceData,
    setUser,
    setTag,
    setContext,
  };
});

vi.mock("@sentry/browser", () => ({
  init: sentryMocks.init,
  withScope: sentryMocks.withScope,
  captureMessage: sentryMocks.captureMessage,
  captureException: sentryMocks.captureException,
  browserTracingIntegration: sentryMocks.browserTracingIntegration,
  getTraceData: sentryMocks.getTraceData,
  setUser: sentryMocks.setUser,
  setTag: sentryMocks.setTag,
  setContext: sentryMocks.setContext,
}));

describe("frontend monitoring", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.unstubAllEnvs();
  });

  it("stays disabled when DSN is missing", async () => {
    vi.stubEnv("VITE_SENTRY_ENABLED", "true");
    vi.stubEnv("VITE_SENTRY_DSN", "");

    const monitoring = await import("../lib/monitoring");
    const enabled = monitoring.initMonitoring();

    expect(enabled).toBe(false);
    expect(monitoring.isMonitoringEnabled()).toBe(false);
    expect(sentryMocks.init).not.toHaveBeenCalled();
  });

  it("respects explicit disable env even when DSN is present", async () => {
    vi.stubEnv("VITE_SENTRY_ENABLED", "false");
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.ingest.sentry.io/1");

    const monitoring = await import("../lib/monitoring");
    const enabled = monitoring.initMonitoring();

    expect(enabled).toBe(false);
    expect(sentryMocks.init).not.toHaveBeenCalled();
  });

  it("redacts sensitive payload and emits telemetry events", async () => {
    vi.stubEnv("VITE_SENTRY_ENABLED", "true");
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.ingest.sentry.io/1");
    vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", "0.2");

    const monitoring = await import("../lib/monitoring");
    const enabled = monitoring.initMonitoring();
    expect(enabled).toBe(true);

    const controller = new AbortController();
    let receivedDetail: unknown = null;
    window.addEventListener(
      "kb-telemetry",
      (event: Event) => {
        receivedDetail = (event as CustomEvent).detail;
      },
      { signal: controller.signal },
    );

    monitoring.trackTelemetry("payment_checkout_error", {
      email: "user@example.com",
      authorization: "Bearer abc",
      status: "failed",
    });

    expect(sentryMocks.captureMessage).toHaveBeenCalledWith("telemetry.payment_checkout_error");
    expect(receivedDetail).toEqual({
      event: "payment_checkout_error",
      payload: {
        email: "[REDACTED]",
        authorization: "[REDACTED]",
        status: "failed",
      },
    });
    controller.abort();
  });

  it("captures uncaught runtime errors when enabled", async () => {
    vi.stubEnv("VITE_SENTRY_ENABLED", "true");
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.ingest.sentry.io/1");

    const monitoring = await import("../lib/monitoring");
    expect(monitoring.initMonitoring()).toBe(true);

    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "Runtime failure",
        error: new Error("runtime boom"),
      }),
    );

    expect(sentryMocks.captureException.mock.calls.length).toBeGreaterThan(0);
  });

  it("provides sentry-trace and baggage headers for API propagation", async () => {
    vi.stubEnv("VITE_SENTRY_ENABLED", "true");
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.ingest.sentry.io/1");

    const monitoring = await import("../lib/monitoring");
    expect(monitoring.initMonitoring()).toBe(true);

    const headers = monitoring.getTracePropagationHeaders();
    expect(headers).toEqual({
      "sentry-trace": "abc123-def456",
      baggage: "sentry-release=test",
    });
  });
});
