export interface PinnedTopic {
    id: string
    title: string
    description: string
}

export interface QueryRequest {
    topic: string
    levels?: string[]
    premium?: boolean
    bypass_cache?: boolean
    mode?: 'fast' | 'ensemble'
    temperature?: number
    regenerate?: boolean
}

export type Mode = 'fast' | 'ensemble'

export interface QueryResponse {
    topic: string
    explanations: Record<string, string>
    cached: boolean
    mode?: Mode
}

export interface HistoryItem {
    id: string
    topic: string
    levels: string[]
    mode: Mode
    created_at: string
}

export interface ExportRequest {
    topic: string
    explanations: Record<string, string>
    format: 'txt' | 'md'
    premium?: boolean
    mode?: Mode
    visuals?: Record<string, string>
}

export const FREE_LEVELS = ['eli5', 'eli10', 'eli12', 'eli15', 'meme-style'] as const
export const PREMIUM_LEVELS = ['classic60', 'gentle70', 'warm80'] as const
export const LEGACY_LEVELS = ['meme'] as const
export const ALL_FREE_LEVELS: readonly string[] = [...FREE_LEVELS, ...LEGACY_LEVELS]
export type Level = (typeof FREE_LEVELS)[number] | (typeof PREMIUM_LEVELS)[number] | (typeof LEGACY_LEVELS)[number]
