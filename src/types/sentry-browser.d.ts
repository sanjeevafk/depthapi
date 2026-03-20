declare module '@sentry/browser' {
  export interface Integration {
    name?: string
  }

  export interface RequestData {
    headers?: Record<string, unknown>
    url?: string
    [key: string]: unknown
  }

  export interface ErrorEvent {
    request?: RequestData
    user?: Record<string, unknown>
    [key: string]: unknown
  }

  export interface Breadcrumb {
    [key: string]: unknown
  }

  export interface Scope {
    setExtra(key: string, value: unknown): void
    setTag(key: string, value: string): void
    setLevel(level: string): void
  }

  export function init(options: {
    dsn: string
    environment?: string
    release?: string
    tracesSampleRate?: number
    sendDefaultPii?: boolean
    integrations?: Integration[]
    tracePropagationTargets?: (string | RegExp)[]
    ignoreErrors?: (string | RegExp)[]
    beforeSend?: (event: ErrorEvent, hint: unknown) => ErrorEvent | null
    beforeBreadcrumb?: (breadcrumb: Breadcrumb, hint: unknown) => Breadcrumb | null
  }): void

  export function browserTracingIntegration(): Integration
  export function getTraceData(): { 'sentry-trace'?: string; baggage?: string }
  export function setUser(user: { id?: string; email?: string } | null): void
  export function setTag(key: string, value: string): void
  export function setContext(key: string, context: Record<string, unknown>): void
  export function withScope(callback: (scope: Scope) => void): void
  export function captureException(error: unknown, captureContext?: Record<string, unknown>): string
  export function captureMessage(
    message: string,
    level?: "fatal" | "error" | "warning" | "log" | "debug" | "info"
  ): string
}
