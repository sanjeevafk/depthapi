import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { QueryResponse, Mode, Level } from '../types'
import { queryTopicStream } from '../api'
import { responseCache } from '../lib/responseCache'

interface KnowBearState {
    // UI State
    loading: boolean
    result: QueryResponse | null
    selectedLevel: Level
    error: string | null
    mode: Mode
    fetchingLevels: Set<Level>
    failedLevels: Set<Level>
    isSidebarOpen: boolean
    activeTopic: string
    isFromCache: boolean
    loadingMeta: { mode: Mode; level: Level; topic: string } | null
    modeSwitching: boolean

    // Abort controller for stream management
    abortController: AbortController | null

    // Actions
    setLoading: (loading: boolean) => void
    setResult: (result: QueryResponse | null) => void
    setSelectedLevel: (level: Level) => void
    setError: (error: string | null) => void
    setMode: (mode: Mode) => void
    setFetchingLevels: (levels: Set<Level>) => void
    setFailedLevels: (levels: Set<Level>) => void
    setIsSidebarOpen: (open: boolean) => void
    setActiveTopic: (topic: string) => void
    setIsFromCache: (fromCache: boolean) => void
    setLoadingMeta: (meta: { mode: Mode; level: Level; topic: string } | null) => void
    setModeSwitching: (switching: boolean) => void

    // Complex actions
    startSearch: (topic: string, forceRefresh?: boolean, requestedMode?: Mode, requestedLevel?: Level, usageGate?: any) => Promise<void>
    fetchLevel: (topic: string, level: Level, mode: Mode, premium: boolean, options?: { temperature?: number; regenerate?: boolean }) => Promise<void>
    abortCurrentStream: () => void
    reset: () => void
}

const initialState = {
    loading: false,
    result: null,
    selectedLevel: 'eli5' as Level,
    error: null,
    mode: 'fast' as Mode,
    fetchingLevels: new Set<Level>(),
    failedLevels: new Set<Level>(),
    isSidebarOpen: true,
    activeTopic: '',
    isFromCache: false,
    loadingMeta: null,
    modeSwitching: false,
    abortController: null,
}

export const useKnowBearStore = create<KnowBearState>()(
    persist(
        (set, get) => ({
            ...initialState,

            // Simple setters
            setLoading: (loading) => set({ loading }),
            setResult: (result) => set({ result }),
            setSelectedLevel: (selectedLevel) => set({ selectedLevel }),
            setError: (error) => set({ error }),
            setMode: (mode) => set({ mode }),
            setFetchingLevels: (fetchingLevels) => set({ fetchingLevels }),
            setFailedLevels: (failedLevels) => set({ failedLevels }),
            setIsSidebarOpen: (isSidebarOpen) => set({ isSidebarOpen }),
            setActiveTopic: (activeTopic) => set({ activeTopic }),
            setIsFromCache: (isFromCache) => set({ isFromCache }),
            setLoadingMeta: (loadingMeta) => set({ loadingMeta }),
            setModeSwitching: (modeSwitching) => set({ modeSwitching }),

            // Abort current stream
            abortCurrentStream: () => {
                const { abortController } = get()
                if (abortController) {
                    console.log('Aborting previous request')
                    abortController.abort()
                }
                const newController = new AbortController()
                set({ abortController: newController })
                return newController
            },

            // Fetch a specific level
            fetchLevel: async (topic: string, level: Level, mode: Mode, premium: boolean, options?: { temperature?: number; regenerate?: boolean }) => {
                console.log('🔍 fetchLevel called:', { topic, level, mode, premium })
                const state = get()

                // Add to fetching set
                const newFetching = new Set(state.fetchingLevels)
                newFetching.add(level)
                set({ fetchingLevels: newFetching })

                // Ensure we have an abort controller
                const controller = state.abortController || new AbortController()
                if (!state.abortController) {
                    set({ abortController: controller })
                }

                try {
                    let streamedText = ''

                    console.log('📡 Starting stream for', level)
                    await queryTopicStream(
                        {
                            topic,
                            levels: [level],
                            mode,
                            premium,
                            temperature: options?.temperature,
                            regenerate: options?.regenerate || false,
                            bypass_cache: options?.regenerate || false
                        },
                        (chunk: string) => {
                            streamedText += chunk
                            const currentResult = get().result
                            if (currentResult) {
                                set({
                                    result: {
                                        ...currentResult,
                                        explanations: {
                                            ...currentResult.explanations,
                                            [level]: streamedText
                                        }
                                    }
                                })
                            } else {
                                console.warn('⚠️ No result object to update')
                            }
                        },
                        () => {
                            console.log('✅ Stream completed for', level)
                            // onDone - cache the response
                            const finalResult = get().result
                            if (finalResult && finalResult.explanations) {
                                responseCache.set(topic, mode, finalResult.explanations)
                                console.log('📦 Cached response:', topic, mode)
                            }
                        },
                        (error: Error) => {
                            console.error(`❌ Failed to fetch ${level}:`, error)
                            const newFailed = new Set(get().failedLevels)
                            newFailed.add(level)
                            set({ failedLevels: newFailed, error: error.message })
                        },
                        controller.signal
                    )
                } catch (err: any) {
                    if (err.name !== 'AbortError') {
                        console.error(`❌ Error fetching ${level}:`, err)
                        const newFailed = new Set(get().failedLevels)
                        newFailed.add(level)
                        set({ failedLevels: newFailed, error: err.message })
                    }
                } finally {
                    // Remove from fetching set
                    const newFetching = new Set(get().fetchingLevels)
                    newFetching.delete(level)
                    set({ fetchingLevels: newFetching })
                }
            },

            // Start a new search
            startSearch: async (topic: string, forceRefresh = false, requestedMode?: Mode, requestedLevel?: Level, usageGate?: any) => {
                if (!topic.trim()) return

                const state = get()

                // Abort previous request
                state.abortCurrentStream()

                const activeMode = requestedMode || state.mode
                const activeLevel = requestedLevel || state.selectedLevel

                // Clear state for fresh feel
                if (!forceRefresh) {
                    set({ result: null, error: null })
                }

                // Sync visual mode indicators
                if (requestedMode && requestedMode !== state.mode) set({ mode: requestedMode })
                if (requestedLevel && requestedLevel !== state.selectedLevel) set({ selectedLevel: activeLevel })

                // Usage gate check (optional, graceful degradation)
                let effectiveMode = activeMode
                const isPro = Boolean(usageGate?.isPro)
                if (usageGate) {
                    const { allowed: searchAllowed, downgraded } = usageGate.checkAction('search', activeMode)
                    if (!searchAllowed) return

                    // Premium mode gating
                    if (activeMode === 'ensemble') {
                        const { allowed } = usageGate.checkAction('premium_mode')
                        if (!allowed) return
                    }

                    // Determine effective mode
                    effectiveMode = downgraded ? 'fast' : activeMode
                    if (downgraded) set({ mode: 'fast' })

                    usageGate.recordAction('search', effectiveMode)
                }

                // Check cache
                if (!forceRefresh) {
                    const cached = responseCache.get(topic, effectiveMode)
                    if (cached) {
                        console.log('Cache hit from localStorage:', topic, effectiveMode)
                        set({
                            result: { topic, explanations: cached.explanations, cached: true, mode: effectiveMode },
                            activeTopic: topic,
                            isFromCache: true,
                            loading: false,
                            error: null,
                            fetchingLevels: new Set()
                        })

                        // Sync mode if different
                        if (cached.mode && cached.mode !== state.mode) set({ mode: cached.mode as Mode })

                        // Find available level
                        if (!cached.explanations[activeLevel]) {
                            const availableLevel = Object.keys(cached.explanations)[0] as Level
                            if (availableLevel) set({ selectedLevel: availableLevel })
                        }

                        setTimeout(() => set({ isFromCache: false }), 3000)
                        return
                    }
                }

                // Start search
                console.log('🚀 Starting search:', { topic, effectiveMode, activeLevel })
                set({
                    activeTopic: topic,
                    loadingMeta: { mode: effectiveMode, level: activeLevel, topic },
                    loading: true,
                    isFromCache: false,
                    error: null
                })

                // Handle regeneration
                if (forceRefresh) {
                    const currentResult = get().result
                    if (currentResult) {
                        set({
                            result: {
                                ...currentResult,
                                explanations: { ...currentResult.explanations, [activeLevel]: '' }
                            }
                        })
                    }

                    const randomTemp = Math.random() * (1.1 - 0.95) + 0.95
                    set({ fetchingLevels: new Set(), failedLevels: new Set() })

                    await get().fetchLevel(topic, activeLevel, effectiveMode, isPro, {
                        temperature: randomTemp,
                        regenerate: true
                    })
                    set({ loading: false, loadingMeta: null })
                    return
                }

                // Fresh search - initialize result object BEFORE calling fetchLevel
                set({
                    result: { topic, explanations: {}, mode: effectiveMode, cached: false },
                    fetchingLevels: new Set(),
                    failedLevels: new Set()
                })

                console.log('📝 Result initialized, calling fetchLevel')
                await get().fetchLevel(topic, activeLevel, effectiveMode, isPro)
                set({ loading: false, loadingMeta: null })
            },

            // Reset to initial state
            reset: () => set(initialState),
        }),
        {
            name: 'knowbear-store',
            partialize: (state) => ({
                // Only persist UI preferences, not transient state
                selectedLevel: state.selectedLevel,
                mode: state.mode,
                isSidebarOpen: state.isSidebarOpen,
            }),
        }
    )
)
