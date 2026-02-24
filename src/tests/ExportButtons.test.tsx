import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ExportButtons from '../components/ExportButtons'

describe('ExportButtons', () => {
    it('renders text and markdown buttons', () => {
        render(<ExportButtons topic="Test" explanations={{}} />)
        expect(screen.getByText(/export \.txt/i)).toBeInTheDocument()
        expect(screen.getByText(/export \.md/i)).toBeInTheDocument()
    })

    it('buttons are clickable', () => {
        render(<ExportButtons topic="Test" explanations={{ eli5: 'test' }} />)
        const txtBtn = screen.getByText(/export \.txt/i)
        expect(txtBtn).not.toBeDisabled()
    })
})
