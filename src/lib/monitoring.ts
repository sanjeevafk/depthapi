import * as Sentry from '@sentry/browser'

const REDACTED = '[REDACTED]'
const SENSITIVE_KEY_PARTS = [
    'authorization',
    'token',
    'password',
    'secret',
    'cookie',
    'api_key',
    'headers',
    'email',
]
const EMAIL_PATTERN = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi
const BEARER_PATTERN = /(bearer\s+)[a-z0-9._-]+/gi

let monitoringInitialized = false
let windowListenersAttached = false

const DEFAULT_TRACE_TARGETS = ['localhost', '/api', import.meta.env.VITE_API_URL || '']
const NOISE_ERROR_PATTERNS = [
    /ResizeObserver loop limit exceeded/i,
    /NetworkError when attempting to fetch resource/i,
    /Failed to fetch dynamically imported module/i,
]

type ScopeLike = {
    setExtra: (key: string, value: unknown) => void
    setTag: (key: string, value: string) => void
    setLevel: (level: string) => void
}

const parseBoolean = (value: string | undefined, defaultValue: boolean): boolean => {
    if (!value) return defaultValue
    const normalized = value.trim().toLowerCase()
    if (['1', 'true', 'yes', 'on'].includes(normalized)) return true
    if (['0', 'false', 'no', 'off'].includes(normalized)) return false
    return defaultValue
}

const looksSensitive = (key: string): boolean => {
    const lowered = key.toLowerCase()
    return SENSITIVE_KEY_PARTS.some((part) => lowered.includes(part))
}

const sanitizeScalar = (value: unknown): unknown => {
    if (typeof value !== 'string') return value
    return value.replace(EMAIL_PATTERN, REDACTED).replace(BEARER_PATTERN, '$1[REDACTED]')
}

export const redactPayload = (value: unknown): unknown => {
    if (Array.isArray(value)) {
        return value.map((item) => redactPayload(item))
    }

    if (value && typeof value === 'object') {
        return Object.entries(value as Record<string, unknown>).reduce<Record<string, unknown>>((acc, [key, inner]) => {
            acc[key] = looksSensitive(key) ? REDACTED : redactPayload(inner)
            return acc
        }, {})
    }

    return sanitizeScalar(value)
}

const createBeforeSend = (event: Sentry.ErrorEvent): Sentry.ErrorEvent => {
    const scrubbed = redactPayload(event) as Sentry.ErrorEvent
    if (scrubbed.request) {
        scrubbed.request.headers = {
            redacted: REDACTED,
        }
        if (scrubbed.request.url) {
            scrubbed.request.url = scrubbed.request.url.split('?')[0]
        }
    }
    if (scrubbed.user) {
        const scrubbedUser = scrubbed.user as Record<string, unknown>
        delete scrubbedUser.email
        delete scrubbedUser.ip_address
        delete scrubbedUser.ipAddress
    }
    return scrubbed
}

const createBeforeBreadcrumb = (breadcrumb: Sentry.Breadcrumb): Sentry.Breadcrumb => {
    return redactPayload(breadcrumb) as Sentry.Breadcrumb
}

export const initMonitoring = (): boolean => {
    if (monitoringInitialized) return true

    const dsn = import.meta.env.VITE_SENTRY_DSN?.trim()
    const enabled = parseBoolean(import.meta.env.VITE_SENTRY_ENABLED, true)

    if (!enabled || !dsn) {
        return false
    }

    const tracesRate = Number.parseFloat(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || '0.1')
    const release = import.meta.env.VITE_SENTRY_RELEASE?.trim()
        || import.meta.env.VITE_APP_VERSION?.trim()
        || undefined

    Sentry.init({
        dsn,
        environment: import.meta.env.MODE,
        release,
        tracesSampleRate: Number.isFinite(tracesRate) ? Math.min(Math.max(tracesRate, 0), 1) : 0.1,
        sendDefaultPii: false,
        integrations: [Sentry.browserTracingIntegration()],
        tracePropagationTargets: DEFAULT_TRACE_TARGETS.filter(Boolean),
        ignoreErrors: NOISE_ERROR_PATTERNS,
        beforeSend: createBeforeSend,
        beforeBreadcrumb: createBeforeBreadcrumb,
    })

    if (typeof window !== 'undefined' && !windowListenersAttached) {
        window.addEventListener('error', (event: ErrorEvent) => {
            if (!monitoringInitialized) return
            const error = event.error instanceof Error
                ? event.error
                : new Error(event.message || 'Unhandled window error')
            captureFrontendError(error, { source: 'window.error', message: event.message })
            if (typeof event.preventDefault === 'function') {
                event.preventDefault()
            }
        })

        window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
            if (!monitoringInitialized) return
            const error = event.reason instanceof Error
                ? event.reason
                : new Error(String(event.reason || 'Unhandled promise rejection'))
            captureFrontendError(error, { source: 'window.unhandledrejection' })
            if (typeof event.preventDefault === 'function') {
                event.preventDefault()
            }
        })

        windowListenersAttached = true
    }

    monitoringInitialized = true
    return true
}

export const isMonitoringEnabled = (): boolean => {
    return monitoringInitialized
}

export const captureFrontendError = (error: Error, context?: Record<string, unknown>): void => {
    if (!monitoringInitialized) return

    const sanitizedContext = redactPayload(context || {}) as Record<string, unknown>
    Sentry.withScope((scope: ScopeLike) => {
        Object.entries(sanitizedContext).forEach(([key, value]) => {
            scope.setExtra(key, value)
        })
        Sentry.captureException(error)
    })
}

export const setMonitoringUser = (user: { id?: string | null } | null): void => {
    if (!monitoringInitialized) return
    if (!user?.id) {
        Sentry.setUser(null)
        return
    }

    Sentry.setUser({
        id: user.id || undefined,
    })
}

export const setMonitoringRoute = (route: string): void => {
    if (!monitoringInitialized || !route) return
    Sentry.setTag('route', route)
    Sentry.setContext('page', { route })
}

export const getTracePropagationHeaders = (): Record<string, string> => {
    if (!monitoringInitialized) return {}
    const traceData = Sentry.getTraceData()
    const headers: Record<string, string> = {}
    const sentryTrace = traceData['sentry-trace']
    const baggage = traceData.baggage
    if (typeof sentryTrace === 'string' && sentryTrace) {
        headers['sentry-trace'] = sentryTrace
    }
    if (typeof baggage === 'string' && baggage) {
        headers.baggage = baggage
    }
    return headers
}

export const trackTelemetry = (eventName: string, payload: Record<string, unknown> = {}): void => {
    const sanitizedPayload = redactPayload(payload) as Record<string, unknown>

    if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('kb-telemetry', {
            detail: {
                event: eventName,
                payload: sanitizedPayload,
            },
        }))
    }

    if (!monitoringInitialized) {
        return
    }

    Sentry.withScope((scope: ScopeLike) => {
        scope.setLevel('info')
        scope.setTag('telemetry_event', eventName)
        Object.entries(sanitizedPayload).forEach(([key, value]) => {
            scope.setExtra(key, value)
        })
        Sentry.captureMessage(`telemetry.${eventName}`)
    })
}
