import { describe, it, expect } from 'vitest'
import { splitSseEvents, parseSseEvent } from '../lib/sse'

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

    it('parses event with data, id, and event', () => {
        const eventBlock = 'id: 7\n' + 'event: delta\n' + 'data: hello\n' + 'data: world\n'
        const parsed = parseSseEvent(eventBlock)
        expect(parsed).toEqual({ id: '7', event: 'delta', data: 'hello\nworld' })
    })

    it('returns null when required fields are missing', () => {
        expect(parseSseEvent('event: ping\n')).toBeNull()
        expect(parseSseEvent('id: 1\ndata: hi\n')).toBeNull()
        expect(parseSseEvent('event: ping\nid: 1\n')).toBeNull()
    })
})
