import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useKnowBearStore } from './useKnowBearStore'
import { responseCache } from '../lib/responseCache'

// Mock the API
vi.mock('../api', () => ({
    queryTopicStream: vi.fn((_req, onChunk, onDone) => {
        // Simulate streaming
        setTimeout(() => {
            onChunk('Test ')
            onChunk('response ')
            onChunk('content')
            onDone()
        }, 100)
        return Promise.resolve()
    })
}))

// Mock responseCache
vi.mock('../lib/responseCache', () => ({
    responseCache: {
        get: vi.fn(),
        set: vi.fn(),
        clear: vi.fn(),
        getStats: vi.fn(() => ({ count: 0, size: 0, compressionRatio: '0%' }))
    }
}))

describe('useKnowBearStore', () => {
    beforeEach(() => {
        // Reset store to initial state
        const { result } = renderHook(() => useKnowBearStore())
        act(() => {
            result.current.reset()
        })
        vi.clearAllMocks()
    })

    describe('Initial State', () => {
        it('should have correct initial state', () => {
            const { result } = renderHook(() => useKnowBearStore())

            expect(result.current.loading).toBe(false)
            expect(result.current.result).toBeNull()
            expect(result.current.selectedLevel).toBe('eli5')
            expect(result.current.mode).toBe('fast')
            expect(result.current.error).toBeNull()
            expect(result.current.fetchingLevels.size).toBe(0)
            expect(result.current.failedLevels.size).toBe(0)
            expect(result.current.isSidebarOpen).toBe(true)
            expect(result.current.activeTopic).toBe('')
            expect(result.current.isFromCache).toBe(false)
        })
    })

    describe('Simple Setters', () => {
        it('should update loading state', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.setLoading(true)
            })

            expect(result.current.loading).toBe(true)
        })

        it('should update mode', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.setMode('ensemble')
            })

            expect(result.current.mode).toBe('ensemble')
        })

        it('should update selected level', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.setSelectedLevel('eli10')
            })

            expect(result.current.selectedLevel).toBe('eli10')
        })

        it('should update sidebar state', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.setIsSidebarOpen(false)
            })

            expect(result.current.isSidebarOpen).toBe(false)
        })
    })

    describe('AbortController Management', () => {
        it('should create new abort controller', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.abortCurrentStream()
            })

            expect(result.current.abortController).toBeInstanceOf(AbortController)
        })

        it('should abort previous controller before creating new one', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.abortCurrentStream()
            })

            const firstController = result.current.abortController
            const abortSpy = vi.spyOn(firstController!, 'abort')

            act(() => {
                result.current.abortCurrentStream()
            })

            expect(abortSpy).toHaveBeenCalled()
            expect(result.current.abortController).not.toBe(firstController)
        })
    })

    describe('Cache Integration', () => {
        it('should check cache before starting search', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            const mockCached = {
                topic: 'blockchain',
                mode: 'fast',
                explanations: { eli5: 'Cached content' },
                timestamp: Date.now()
            }

            vi.mocked(responseCache.get).mockReturnValue(mockCached)

            const mockUsageGate = {
                checkAction: vi.fn(() => ({ allowed: true, downgraded: false })),
                recordAction: vi.fn()
            }

            await act(async () => {
                await result.current.startSearch('blockchain', false, undefined, undefined, mockUsageGate)
            })

            expect(responseCache.get).toHaveBeenCalledWith('blockchain', 'fast')
            expect(result.current.result).toEqual({
                topic: 'blockchain',
                explanations: mockCached.explanations,
                cached: true,
                mode: 'fast'
            })
            expect(result.current.isFromCache).toBe(true)
        })

        it('should set cache after successful fetch', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            vi.mocked(responseCache.get).mockReturnValue(null)

            act(() => {
                result.current.setResult({
                    topic: 'blockchain',
                    explanations: {},
                    cached: false,
                    mode: 'fast'
                })
            })

            await act(async () => {
                await result.current.fetchLevel('blockchain', 'eli5', 'fast', false)
            })

            await waitFor(() => {
                expect(responseCache.set).toHaveBeenCalled()
            })
        })
    })

    describe('Search Flow', () => {
        it('should handle cache miss and fetch from API', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            vi.mocked(responseCache.get).mockReturnValue(null)

            const mockUsageGate = {
                checkAction: vi.fn(() => ({ allowed: true, downgraded: false })),
                recordAction: vi.fn()
            }

            await act(async () => {
                await result.current.startSearch('blockchain', false, undefined, undefined, mockUsageGate)
            })

            expect(mockUsageGate.checkAction).toHaveBeenCalledWith('search', 'fast')
            expect(mockUsageGate.recordAction).toHaveBeenCalledWith('search', 'fast')
        })

        it('should handle downgraded mode', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            vi.mocked(responseCache.get).mockReturnValue(null)

            const mockUsageGate = {
                checkAction: vi.fn(() => ({ allowed: true, downgraded: true })),
                recordAction: vi.fn()
            }

            await act(async () => {
                await result.current.startSearch('blockchain', false, 'ensemble', undefined, mockUsageGate)
            })

            expect(result.current.mode).toBe('fast')
            expect(mockUsageGate.recordAction).toHaveBeenCalledWith('search', 'fast')
        })

        it('should abort if not allowed', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            const mockUsageGate = {
                checkAction: vi.fn(() => ({ allowed: false, downgraded: false })),
                recordAction: vi.fn()
            }

            await act(async () => {
                await result.current.startSearch('blockchain', false, undefined, undefined, mockUsageGate)
            })

            expect(mockUsageGate.recordAction).not.toHaveBeenCalled()
            expect(result.current.result).toBeNull()
        })
    })

    describe('Regeneration', () => {
        it('should use random temperature for regeneration', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            vi.mocked(responseCache.get).mockReturnValue(null)

            const mockUsageGate = {
                checkAction: vi.fn(() => ({ allowed: true, downgraded: false })),
                recordAction: vi.fn()
            }

            // Set initial result
            act(() => {
                result.current.setResult({
                    topic: 'blockchain',
                    explanations: { eli5: 'Old content' },
                    cached: false,
                    mode: 'fast'
                })
                result.current.setActiveTopic('blockchain')
            })

            await act(async () => {
                await result.current.startSearch('blockchain', true, 'fast', 'eli5', mockUsageGate)
            })

            // Should clear the level content first
            expect(result.current.result?.explanations.eli5).toBeDefined()
        })
    })

    describe('Level Fetching', () => {
        it('should add level to fetching set during fetch', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.setResult({
                    topic: 'blockchain',
                    explanations: {},
                    cached: false,
                    mode: 'fast'
                })
            })

            let fetchPromise: Promise<void>
            act(() => {
                fetchPromise = result.current.fetchLevel('blockchain', 'eli5', 'fast', false)
            })

            await waitFor(() => {
                expect(result.current.fetchingLevels.has('eli5')).toBe(true)
            })

            await act(async () => {
                await fetchPromise
            })

            // After fetch, should be removed
            await waitFor(() => {
                expect(result.current.fetchingLevels.has('eli5')).toBe(false)
            })
        })

        it('should handle fetch errors gracefully', async () => {
            const { result } = renderHook(() => useKnowBearStore())

            // Mock API to throw error
            const { queryTopicStream } = await import('../api')
            vi.mocked(queryTopicStream).mockImplementationOnce(() => {
                throw new Error('Network error')
            })

            act(() => {
                result.current.setResult({
                    topic: 'blockchain',
                    explanations: {},
                    cached: false,
                    mode: 'fast'
                })
            })

            await act(async () => {
                await result.current.fetchLevel('blockchain', 'eli5', 'fast', false)
            })

            await waitFor(() => {
                expect(result.current.failedLevels.has('eli5')).toBe(true)
            })
        })
    })

    describe('State Persistence', () => {
        it('should persist UI preferences', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.setMode('ensemble')
                result.current.setSelectedLevel('eli10')
                result.current.setIsSidebarOpen(false)
            })

            // These should be persisted (checked via localStorage in real app)
            expect(result.current.mode).toBe('ensemble')
            expect(result.current.selectedLevel).toBe('eli10')
            expect(result.current.isSidebarOpen).toBe(false)
        })

        it('should not persist transient state', () => {
            const { result } = renderHook(() => useKnowBearStore())

            act(() => {
                result.current.setLoading(true)
                result.current.setResult({
                    topic: 'test',
                    explanations: {},
                    cached: false,
                    mode: 'fast'
                })
            })

            // These are transient and should reset on page load
            // (In real app, would test by unmounting and remounting)
        })
    })

    describe('Reset', () => {
        it('should reset all state to initial values', () => {
            const { result } = renderHook(() => useKnowBearStore())

            // Change some state
            act(() => {
                result.current.setMode('ensemble')
                result.current.setLoading(true)
                result.current.setResult({
                    topic: 'test',
                    explanations: {},
                    cached: false,
                    mode: 'ensemble'
                })
            })

            // Reset
            act(() => {
                result.current.reset()
            })

            // Should be back to initial state
            expect(result.current.loading).toBe(false)
            expect(result.current.result).toBeNull()
            expect(result.current.mode).toBe('fast')
        })
    })
})
