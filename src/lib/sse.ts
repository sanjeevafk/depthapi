export interface SseParseResult {
    events: string[]
    remainder: string
}

export interface ParsedSseEvent {
    id: string
    event: string
    data: string
}

export const splitSseEvents = (buffer: string): SseParseResult => {
    const normalized = buffer.replace(/\r/g, '')
    const parts = normalized.split('\n\n')
    return {
        events: parts.slice(0, -1),
        remainder: parts[parts.length - 1] ?? '',
    }
}

export const parseSseEvent = (eventBlock: string): ParsedSseEvent | null => {
    const lines = eventBlock.split('\n')
    const dataLines: string[] = []
    let event: string | null = null
    let id: string | null = null

    for (const line of lines) {
        if (!line) continue
        if (line.startsWith(':')) continue
        if (line.startsWith('event:')) {
            event = line.slice(6).trim()
            continue
        }
        if (line.startsWith('id:')) {
            id = line.slice(3).trim()
            continue
        }
        if (line.startsWith('data:')) {
            const value = line.slice(5)
            dataLines.push(value.startsWith(' ') ? value.slice(1) : value)
        }
    }

    if (!event || !id || dataLines.length === 0) return null
    return { event, id, data: dataLines.join('\n') }
}
