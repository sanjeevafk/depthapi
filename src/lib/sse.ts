export interface SseParseResult {
    events: string[]
    remainder: string
}

export const splitSseEvents = (buffer: string): SseParseResult => {
    const normalized = buffer.replace(/\r/g, '')
    const parts = normalized.split('\n\n')
    return {
        events: parts.slice(0, -1),
        remainder: parts[parts.length - 1] ?? '',
    }
}

export const extractSseData = (eventBlock: string): string | null => {
    const lines = eventBlock.split('\n')
    const dataLines: string[] = []

    for (const line of lines) {
        if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trimStart())
        }
    }

    if (dataLines.length === 0) return null
    return dataLines.join('\n')
}
