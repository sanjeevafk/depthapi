export const CHAT_MODES = [
    'eli5',
    'eli10',
    'eli12',
    'eli15',
    'meme-style',
    'classic60',
    'gentle70',
    'warm80',
    'ensemble',
    'technical-depth',
    'socratic',
] as const

export const CHAT_FREE_MODES = [
    'eli5',
    'eli10',
    'eli12',
    'eli15',
    'meme-style',
    'ensemble',
    'technical-depth',
    'socratic',
] as const

export const CHAT_PREMIUM_MODES = [
    'classic60',
    'gentle70',
    'warm80',
] as const

export const CHAT_PROMPT_MODES = [
    'eli5',
    'eli10',
    'eli12',
    'eli15',
    'meme-style',
    'classic60',
    'gentle70',
    'warm80',
] as const

export const LEGACY_CHAT_MODES = [
    'technical',
    'meme',
] as const

export type ChatMode = (typeof CHAT_MODES)[number]
export type PromptMode = (typeof CHAT_PROMPT_MODES)[number]
export type LegacyChatMode = (typeof LEGACY_CHAT_MODES)[number]
export type ConversationMode = ChatMode | LegacyChatMode

export interface Conversation {
    id: string
    title: string
    mode: ConversationMode
    settings: any
    created_at: string
    updated_at: string
}

export interface MessageMetadata {
    client_id?: string
    assistant_client_id?: string
    mode?: ConversationMode
    prompt_mode?: PromptMode
    [key: string]: any
}

export interface Message {
    id: string
    role: 'user' | 'assistant'
    content: string
    attachments?: any[]
    metadata?: MessageMetadata
    created_at: string
    clientGeneratedId?: string
    serverMessageId?: string
    isStreaming?: boolean
    error?: string
}
