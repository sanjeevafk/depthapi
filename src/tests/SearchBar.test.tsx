import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SearchBar from '../components/SearchBar'

vi.mock('../hooks/useUsageGate', () => ({
    useUsageGate: () => ({
        checkAction: () => ({ allowed: true }),
        recordAction: () => {},
        showPremiumModal: false,
        setShowPremiumModal: () => {},
        paywallContext: null,
        upgradeToPro: () => {},
        isPro: false,
        deepDiveUsageCount: 0,
        deepDiveLimit: 0
    })
}))

describe('SearchBar', () => {
    it('renders input and button', () => {
        render(<SearchBar onSearch={() => { }} loading={false} mode="fast" onModeChange={() => { }} />)
        expect(screen.getByPlaceholderText(/what passes for knowledge/i)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: /explain/i })).toBeInTheDocument()
    })

    it('calls onSearch with topic on submit', () => {
        const onSearch = vi.fn()
        render(<SearchBar onSearch={onSearch} loading={false} mode="fast" onModeChange={() => { }} />)
        const input = screen.getByPlaceholderText(/what passes for knowledge/i)
        fireEvent.change(input, { target: { value: 'Photosynthesis' } })
        fireEvent.submit(input.closest('form')!)
        expect(onSearch).toHaveBeenCalledWith('Photosynthesis')
    })

    it('disables button when loading', () => {
        render(<SearchBar onSearch={() => { }} loading mode="fast" onModeChange={() => { }} />)
        const submitButton = screen.getAllByRole('button').find(button => button.getAttribute('type') === 'submit')
        expect(submitButton).toBeTruthy()
        expect(submitButton).toBeDisabled()
    })
})
