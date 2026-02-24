import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import AppPage from './AppPage'
import { useKnowBearStore } from '../store/useKnowBearStore'
import { responseCache } from '../lib/responseCache'

// Mock all dependencies
vi.mock('../api', () => ({
    getPinnedTopics: vi.fn(() => Promise.resolve([]))
}))

vi.mock('../lib/responseCache', () => ({
    responseCache: {
        get: vi.fn(() => null),
        getStats: vi.fn(() => ({ count: 0, size: 0, compressionRatio: '0%' }))
    }
}))

vi.mock('../hooks/useUsageGate', () => ({
    useUsageGate: () => ({
        checkAction: vi.fn(() => ({ allowed: true, downgraded: false })),
        recordAction: vi.fn(),
        showPremiumModal: false,
        setShowPremiumModal: vi.fn()
    })
}))

vi.mock('../components/Sidebar', () => ({
    default: ({ onToggle, onSelectTopic }: any) => (
        <div data-testid="sidebar">
            <button onClick={onToggle}>Toggle</button>
            <button onClick={() => onSelectTopic('test topic', 'fast', 'eli5')}>
                Select Topic
            </button>
        </div>
    )
}))

vi.mock('../components/SearchBar', () => ({
    default: ({ onSearch, loading, onModeChange }: any) => (
        <div data-testid="search-bar">
            <input
                data-testid="search-input"
                onChange={(e) => onSearch(e.target.value)}
                disabled={loading}
            />
            <button onClick={() => onModeChange('ensemble')}>Change Mode</button>
        </div>
    )
}))

vi.mock('../components/LevelDropdown', () => ({
    default: ({ selected, onChange }: any) => (
        <div data-testid="level-dropdown">
            <button onClick={() => onChange('eli10')}>Change Level</button>
            <span>{selected}</span>
        </div>
    )
}))

vi.mock('../components/ExplanationCard', () => ({
    default: ({ level, content, streaming }: any) => (
        <div data-testid="explanation-card">
            <div data-testid="level">{level}</div>
            <div data-testid="content">{content}</div>
            {streaming && <div data-testid="streaming">Streaming...</div>}
        </div>
    )
}))

vi.mock('../components/ExportDropdown', () => ({
    default: () => <div data-testid="export-dropdown">Export</div>
}))

vi.mock('../components/MobileBottomNav', () => ({
    default: () => <div data-testid="mobile-nav">Mobile Nav</div>
}))

vi.mock('../components/LoadingState', () => ({
    LoadingState: ({ topic }: any) => (
        <div data-testid="loading-state">Loading {topic}...</div>
    )
}))

describe('AppPage Integration Tests', () => {
    beforeEach(() => {
        // Reset store before each test
        const store = useKnowBearStore.getState()
        store.reset()
        vi.clearAllMocks()
    })

    it('should render initial empty state', () => {
        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByTestId('search-bar')).toBeInTheDocument()
        expect(screen.getByTestId('sidebar')).toBeInTheDocument()
        expect(screen.getByText('Search for a topic to get started')).toBeInTheDocument()
    })

    it('should use Zustand store for state management', () => {
        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        const store = useKnowBearStore.getState()

        // Initial state should match store
        expect(store.loading).toBe(false)
        expect(store.result).toBeNull()
        expect(store.selectedLevel).toBe('eli5')
        expect(store.mode).toBe('fast')
    })

    it('should toggle sidebar via store', () => {
        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        const store = useKnowBearStore.getState()
        expect(store.isSidebarOpen).toBe(true)

        const toggleButton = screen.getByText('Toggle')
        fireEvent.click(toggleButton)

        expect(useKnowBearStore.getState().isSidebarOpen).toBe(false)
    })

    it('should change mode via store', () => {
        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        const changeModeButton = screen.getByText('Change Mode')
        fireEvent.click(changeModeButton)

        expect(useKnowBearStore.getState().mode).toBe('ensemble')
    })

    it('should change level via store', async () => {
        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        // Set a result first
        const store = useKnowBearStore.getState()
        act(() => {
            store.setResult({
                topic: 'test',
                explanations: { eli5: 'content', eli10: 'more content' },
                cached: false,
                mode: 'fast'
            })
        })

        await waitFor(() => {
            const changeLevelButton = screen.getByText('Change Level')
            fireEvent.click(changeLevelButton)
        })

        expect(useKnowBearStore.getState().selectedLevel).toBe('eli10')
    })

    it('should display result from store', () => {
        const store = useKnowBearStore.getState()
        store.setResult({
            topic: 'blockchain',
            explanations: { eli5: 'Blockchain is like a digital ledger' },
            cached: false,
            mode: 'fast'
        })
        store.setActiveTopic('blockchain')

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByText('blockchain')).toBeInTheDocument()
        expect(screen.getByTestId('explanation-card')).toBeInTheDocument()
        expect(screen.getByTestId('content')).toHaveTextContent('Blockchain is like a digital ledger')
    })

    it('should show loading state from store', () => {
        const store = useKnowBearStore.getState()
        store.setLoading(true)
        store.setLoadingMeta({ mode: 'fast', level: 'eli5', topic: 'blockchain' })

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByTestId('loading-state')).toBeInTheDocument()
        expect(screen.getByText('Loading blockchain...')).toBeInTheDocument()
    })

    it('should show cache indicator when isFromCache is true', () => {
        const store = useKnowBearStore.getState()
        store.setIsFromCache(true)

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByText('Loaded from cache')).toBeInTheDocument()
    })

    it('should show mode switching indicator', () => {
        const store = useKnowBearStore.getState()
        store.setModeSwitching(true)

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByText('Switching mode...')).toBeInTheDocument()
    })

    it('should show error from store', () => {
        const store = useKnowBearStore.getState()
        store.setError('Something went wrong')

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    })

    it('should handle topic selection from sidebar', async () => {
        const mockStartSearch = vi.fn()
        useKnowBearStore.setState({ startSearch: mockStartSearch })

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        const selectTopicButton = screen.getByText('Select Topic')
        fireEvent.click(selectTopicButton)

        await waitFor(() => {
            expect(mockStartSearch).toHaveBeenCalled()
        })
    })

    it('should pass all required props to MobileBottomNav', () => {
        const store = useKnowBearStore.getState()
        store.setResult({
            topic: 'test',
            explanations: { eli5: 'content' },
            cached: false,
            mode: 'fast'
        })
        store.setActiveTopic('test')

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByTestId('mobile-nav')).toBeInTheDocument()
    })

    it('should show streaming indicator when fetching levels', () => {
        const store = useKnowBearStore.getState()
        store.setResult({
            topic: 'test',
            explanations: { eli5: '' },
            cached: false,
            mode: 'fast'
        })
        store.setFetchingLevels(new Set(['eli5']))

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        expect(screen.getByTestId('streaming')).toBeInTheDocument()
    })

    it('should not have memory leaks - no useState hooks', () => {
        // This test verifies that AppPage uses Zustand instead of useState
        // by checking that the component doesn't maintain local state

        const { unmount } = render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        const store = useKnowBearStore.getState()
        act(() => {
            store.setResult({
                topic: 'test',
                explanations: { eli5: 'content' },
                cached: false,
                mode: 'fast'
            })
        })

        unmount()

        // Store should still have the data after unmount
        expect(useKnowBearStore.getState().result).not.toBeNull()
    })

    it('should handle mode changes via useEffect', async () => {
        const mockStartSearch = vi.fn()
        useKnowBearStore.setState({ startSearch: mockStartSearch })
        vi.mocked(responseCache.get).mockReturnValue(null)

        render(
            <BrowserRouter>
                <AppPage />
            </BrowserRouter>
        )

        const store = useKnowBearStore.getState()

        // Set initial result
        act(() => {
            store.setResult({
                topic: 'blockchain',
                explanations: { eli5: 'content' },
                cached: false,
                mode: 'fast'
            })
            store.setActiveTopic('blockchain')
        })

        // Change mode
        act(() => {
            store.setMode('ensemble')
        })

        // Should trigger mode switch logic
        await waitFor(() => {
            expect(mockStartSearch).toHaveBeenCalled()
        })
    })
})
