import { useState, useEffect } from 'react'
import type { Mode, Level } from '../types'
import { Loader2, Quote } from 'lucide-react'
import { FALLBACK_CACHE_KEY, FALLBACK_QUOTES } from './loadingStateConstants'

interface LoadingStateProps {
    mode: Mode
    level: Level
    topic: string
}

const FALLBACK_CACHE_TTL_MS = 10 * 60 * 1000
const QUOTE_TIMEOUT_MS = 1000

type CachedFallback = {
    quote: string
    savedAt: number
}

const readFallbackCache = (): CachedFallback | null => {
    if (typeof window === 'undefined') return null
    try {
        const raw = window.localStorage.getItem(FALLBACK_CACHE_KEY)
        if (!raw) return null
        const parsed = JSON.parse(raw) as CachedFallback
        if (typeof parsed?.quote !== 'string' || typeof parsed?.savedAt !== 'number') return null
        return parsed
    } catch {
        return null
    }
}

const saveFallbackCache = (quote: string) => {
    if (typeof window === 'undefined') return
    const payload: CachedFallback = { quote, savedAt: Date.now() }
    window.localStorage.setItem(FALLBACK_CACHE_KEY, JSON.stringify(payload))
}

const clearFallbackCache = () => {
    if (typeof window === 'undefined') return
    window.localStorage.removeItem(FALLBACK_CACHE_KEY)
}

export function LoadingState({ mode, level, topic }: LoadingStateProps): JSX.Element {
    const [message, setMessage] = useState('')
    const [quote, setQuote] = useState<string | null>(null)

    useEffect(() => {
        let baseMessage = 'Generating your explanation...'

        if (level === 'eli5') {
            baseMessage = 'Brewing your ELI5 explanation...'
        } else if (level === 'eli10') {
            baseMessage = 'Preparing a simple 10-year-old friendly answer...'
        } else if (mode === 'technical') {
            baseMessage = 'Researching and judging a technical answer...'
        } else if (mode === 'socratic') {
            baseMessage = 'Preparing a guided question sequence...'
        } else {
            baseMessage = 'Crafting your answer...'
        }

        // Bonus hints
        const lowerTopic = topic.toLowerCase()
        if (lowerTopic.includes('diagram') || lowerTopic.includes('architecture') || lowerTopic.includes('flow') || lowerTopic.includes('sequence')) {
            baseMessage += ' and generating diagrams'
        } else if (lowerTopic.includes('code') || lowerTopic.includes('python') || lowerTopic.includes('javascript') || lowerTopic.includes('algorithm')) {
            baseMessage += ' including code examples'
        }

        setMessage(baseMessage)

        // Fetch random quote
        let disposed = false
        let controller: AbortController | null = null
        let timeoutId: number | null = null

        const fetchQuote = async () => {
            const cachedFallback = readFallbackCache()
            const cachedFresh =
                cachedFallback && Date.now() - cachedFallback.savedAt < FALLBACK_CACHE_TTL_MS
            const randomFallback =
                cachedFallback?.quote ||
                FALLBACK_QUOTES[Math.floor(Math.random() * FALLBACK_QUOTES.length)]

            setQuote(randomFallback)

            if (cachedFresh) return

            controller = new AbortController()
            timeoutId = window.setTimeout(() => {
                controller?.abort()
            }, QUOTE_TIMEOUT_MS)

            try {
                const response = await fetch('https://api.quotable.io/random?tags=education|knowledge|learning|science|wisdom|research|effort|creativity&maxLength=100', {
                    signal: controller.signal,
                })
                if (!response.ok) throw new Error('Quote API failed')
                const data = await response.json()
                if (data.content && data.author) {
                    if (disposed) return
                    setQuote(`«${data.content}» — ${data.author}`)
                    clearFallbackCache()
                } else {
                    throw new Error('Invalid quote data')
                }
            } catch {
                // Silently fall back to local quotes (API has SSL issues)
                if (disposed) return
                setQuote(randomFallback)
                saveFallbackCache(randomFallback)
            } finally {
                if (timeoutId) {
                    window.clearTimeout(timeoutId)
                }
            }
        }

        fetchQuote()

        return () => {
            disposed = true
            if (timeoutId) {
                window.clearTimeout(timeoutId)
            }
            if (controller) {
                controller.abort()
            }
        }
    }, [mode, level, topic])

    return (
        <div className="flex flex-col items-center justify-center p-12 min-h-[400px] animate-in fade-in duration-700">
            <div className="relative mb-8">
                <div className="absolute inset-0 bg-cyan-500/20 blur-3xl rounded-full scale-150 animate-pulse"></div>
                <Loader2 className="w-16 h-16 text-cyan-500 animate-spin relative z-10" />
            </div>

            <div className="text-center space-y-6 max-w-lg">
                <div className="space-y-2">
                    <p className="text-2xl font-black text-white tracking-tight animate-pulse">
                        {message}
                        <span className="inline-flex w-8 text-left ml-0.5">
                            <span className="animate-[ellipsis_1.5s_infinite]">...</span>
                        </span>
                    </p>
                    <p className="text-sm text-cyan-400/70 font-medium uppercase tracking-[0.2em]">
                        Meanwhile
                    </p>
                </div>

                {quote && (
                    <div className="relative p-6 bg-white/[0.03] border border-white/10 rounded-2xl animate-in zoom-in-95 duration-500 delay-200">
                        <Quote className="absolute -top-3 -left-3 w-8 h-8 text-white/5 rotate-180" />
                        <p className="text-gray-300 italic leading-relaxed text-sm md:text-base font-medium">
                            {quote}
                        </p>
                        <Quote className="absolute -bottom-3 -right-3 w-8 h-8 text-white/5" />
                    </div>
                )}

                <p className="text-xs text-gray-500 max-w-sm mx-auto leading-relaxed border-t border-white/5 pt-4">
                    Synthesizing knowledge for the perfect explanation.
                </p>
            </div>

            <style>{`
                @keyframes ellipsis {
                    0% { content: '.'; opacity: 0; }
                    33% { content: '..'; opacity: 0.5; }
                    66% { content: '...'; opacity: 1; }
                    100% { content: '.'; opacity: 0; }
                }
            `}</style>
        </div>
    )
}
