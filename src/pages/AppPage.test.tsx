import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import AppPage from './AppPage'

vi.mock('./ChatPage', () => ({
    default: () => <div data-testid="chat-page">ChatPage</div>,
}))

describe('AppPage', () => {
    it('renders ChatPage', () => {
        render(<AppPage />)
        expect(screen.getByTestId('chat-page')).toBeInTheDocument()
    })
})
