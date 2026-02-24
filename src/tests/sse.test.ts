import { describe, it, expect } from 'vitest'
import { splitSseEvents, extractSseData } from '../lib/sse'

describe('sse utils', () => {
    it('splits complete events and remainder', () => {
        const buffer = 'data: {"delta":"hi"}\n\n'
        const result = splitSseEvents(buffer)
        expect(result.events).toHaveLength(1)
        expect(result.events[0]).toBe('data: {"delta":"hi"}')
        expect(result.remainder).toBe('')
    })

    it('keeps incomplete event as remainder', () => {
        const buffer = 'data: one\n\n' + 'data: two'
        const result = splitSseEvents(buffer)
        expect(result.events).toHaveLength(1)
        expect(result.events[0]).toBe('data: one')
        expect(result.remainder).toBe('data: two')
    })

    it('normalizes CRLF', () => {
        const buffer = 'data: hi\r\n\r\n'
        const result = splitSseEvents(buffer)
        expect(result.events).toEqual(['data: hi'])
        expect(result.remainder).toBe('')
    })

    it('extracts data payload lines', () => {
        const eventBlock = 'event: delta\n' + 'data: hello\n' + 'data: world\n'
        const data = extractSseData(eventBlock)
        expect(data).toBe('hello\nworld')
    })

    it('returns null when no data lines', () => {
        const data = extractSseData('event: ping\n')
        expect(data).toBeNull()
    })
})
