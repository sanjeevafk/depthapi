export interface Conversation {
    id: string
    title: string
    mode: 'eli5' | 'ensemble' | 'technical' | 'socratic'
    settings: any
    created_at: string
    updated_at: string
}

export interface Message {
    id: string
    role: 'user' | 'assistant'
    content: string
    attachments?: any[]
    metadata?: any
    created_at: string
    clientGeneratedId?: string
    serverMessageId?: string
    isStreaming?: boolean
    error?: string
}
