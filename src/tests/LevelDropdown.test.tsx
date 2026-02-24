import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import LevelDropdown from '../components/LevelDropdown'

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


describe('LevelDropdown', () => {
    it('renders trigger button with selected label', () => {
        render(<LevelDropdown selected="eli5" onChange={() => { }} />)
        expect(screen.getByText(/like i'm 5/i)).toBeInTheDocument()
        // Should not show other options yet
        expect(screen.queryByText(/like i'm 10/i)).not.toBeInTheDocument()
    })

    it('opens menu and calls onChange when option clicked', () => {
        const onChange = vi.fn()
        render(<LevelDropdown selected="eli5" onChange={onChange} />)

        // Open dropdown
        fireEvent.click(screen.getByRole('button'))

        // Check options are visible
        const option = screen.getByText(/like i'm 10/i)
        expect(option).toBeInTheDocument()

        // Select option
        fireEvent.click(option)
        expect(onChange).toHaveBeenCalledWith('eli10')

        // Menu should close (option not visible)
        expect(screen.queryByText(/like i'm 10/i)).not.toBeInTheDocument()
    })

    it('highlights selected level in menu', () => {
        render(<LevelDropdown selected="eli5" onChange={() => { }} />)

        // Open dropdown
        fireEvent.click(screen.getByRole('button'))

        // Check highlight class
        const selectedOption = screen.getAllByText(/like i'm 5/i)[1] // 0 is trigger, 1 is option
        expect(selectedOption.closest('button')).toHaveClass('text-accent-primary')
    })
})
