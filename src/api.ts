import type { PinnedTopic, QueryRequest, QueryResponse, ExportRequest } from './types'

const API_URL = import.meta.env.VITE_API_URL || ''
const SUPABASE_CONFIGURED = Boolean(import.meta.env.VITE_SUPABASE_URL) && Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY)

import { supabase } from './lib/supabase'

async function fetchAPI<T>(path: string, options?: RequestInit & { responseType?: 'json' | 'blob' }): Promise<T> {
    const { data: { session } } = SUPABASE_CONFIGURED
        ? await supabase.auth.getSession()
        : { data: { session: null } as any }
    const headers: HeadersInit = {
        'Content-Type': 'application/json',
        ...options?.headers,
    }

    if (session?.access_token) {
        // @ts-ignore - HeadersInit type flexibility
        headers['Authorization'] = `Bearer ${session.access_token}`
    }

    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 90000) // 90 seconds

    try {
        const res = await fetch(`${API_URL}${path}`, {
            ...options,
            headers,
            signal: controller.signal,
        })
        clearTimeout(timeoutId)

        if (res.status === 429) throw new Error('You are sending requests too quickly. Please wait a moment.')
        if (!res.ok) throw new Error(`API error: ${res.status}`)

        if (options?.responseType === 'blob') {
            return await res.blob() as unknown as T
        }
        return await res.json()
    } catch (err: any) {
        clearTimeout(timeoutId)
        if (err.name === 'AbortError') throw new Error('Request timed out. Please try again.')
        throw err
    }
}

export async function getPinnedTopics(): Promise<PinnedTopic[]> {
    return fetchAPI('/api/pinned')
}

export async function queryTopic(req: QueryRequest): Promise<QueryResponse> {
    return fetchAPI('/api/query', {
        method: 'POST',
        body: JSON.stringify(req),
    })
}

export async function queryTopicStream(
    req: QueryRequest,
    onChunk: (chunk: string) => void,
    onDone: (data: any) => void,
    onError: (err: any) => void,
    signal?: AbortSignal
) {
    const { data: { session } } = SUPABASE_CONFIGURED
        ? await supabase.auth.getSession()
        : { data: { session: null } as any }
    const headers: HeadersInit = {
        'Content-Type': 'application/json',
    }
    if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`
    }

    let retries = 0
    const maxRetries = 2

    const fallbackToNonStream = async (reason: string): Promise<void> => {
        try {
            console.warn('Streaming unavailable, falling back to non-stream response:', reason)
            const data = await queryTopic(req)
            const preferredLevel = req.levels?.[0]
            const levelKey = preferredLevel && data.explanations?.[preferredLevel]
                ? preferredLevel
                : Object.keys(data.explanations || {})[0]
            const fullText = levelKey ? data.explanations[levelKey] : ''
            if (fullText) {
                onChunk(fullText)
            }
            onDone(data)
        } catch (err) {
            onError(err)
        }
    }

    const attemptStream = async (): Promise<void> => {
        try {
            const response = await fetch(`${API_URL}/api/query/stream`, {
                method: 'POST',
                headers,
                body: JSON.stringify(req),
                signal,
            })

            if (!response.ok) {
                throw new Error(`API error: ${response.status}`)
            }

            // Validate SSE content type
            const contentType = response.headers.get('content-type')
            if (!contentType?.includes('text/event-stream')) {
                return fallbackToNonStream(`Invalid content-type: ${contentType || 'unknown'}`)
            }

            const reader = response.body?.getReader()
            const decoder = new TextDecoder()

            if (!reader) {
                return fallbackToNonStream('ReadableStream not supported')
            }

            let buffer = ''
            const READ_TIMEOUT_MS = 20000

            while (true) {
                const { done, value } = await Promise.race([
                    reader.read(),
                    new Promise<ReadableStreamReadResult<Uint8Array>>((_, reject) =>
                        setTimeout(() => reject(new Error('Stream read timed out')), READ_TIMEOUT_MS)
                    )
                ])
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6).trim()
                        if (data === '[DONE]') {
                            onDone({})
                            return
                        }
                        try {
                            const parsed = JSON.parse(data)
                            if (parsed.chunk) {
                                onChunk(parsed.chunk)
                            } else if (parsed.warning) {
                                // Display warning as part of the response
                                onChunk(`\n\n${parsed.warning}`)
                            } else if (parsed.error) {
                                onError(new Error(parsed.error))
                                return
                            }
                        } catch (e) {
                            console.warn('Failed to parse SSE chunk:', data.substring(0, 100), e)
                            // Don't fail entire stream on one bad chunk
                        }
                    }
                }
            }

            // Flush remaining buffer if stream ended without [DONE]
            if (buffer.trim()) {
                console.warn('Stream ended with incomplete data in buffer:', buffer.substring(0, 100))
            }

        } catch (err: any) {
            if (err.name === 'AbortError') {
                console.log('Stream aborted by user')
                return
            }

            if (err.message === 'Stream read timed out') {
                try {
                    return await fallbackToNonStream(err.message)
                } catch {
                    // fall through to error handling
                }
            }

            // Retry on network errors if not aborted
            if (retries < maxRetries && !signal?.aborted) {
                retries++
                console.warn(`Stream failed, retry ${retries}/${maxRetries}:`, err.message)
                await new Promise(r => setTimeout(r, 1000 * retries))
                return attemptStream()
            }

            await fallbackToNonStream(err.message || 'Stream failed')
        }
    }

    await attemptStream()
}

export async function exportExplanations(req: ExportRequest): Promise<Blob> {
    return fetchAPI('/api/export', {
        method: 'POST',
        body: JSON.stringify(req),
        responseType: 'blob'
    })
}

export async function getHistory(): Promise<any[]> {
    return fetchAPI('/api/history')
}

export async function deleteHistoryItem(id: string): Promise<void> {
    return fetchAPI(`/api/history/${id}`, { method: 'DELETE' })
}

export async function clearHistory(): Promise<void> {
    return fetchAPI('/api/history', { method: 'DELETE' })
}
