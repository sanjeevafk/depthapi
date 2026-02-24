import { create } from 'zustand'
import { supabase } from '../lib/supabase'
import { splitSseEvents, extractSseData } from '../lib/sse'
import type { Conversation, Message } from '../types/chat'

interface ChatState {
    conversations: Conversation[]
    currentConversationId: string | null
    messages: Message[]
    isLoading: boolean
    fetchConversations: () => Promise<void>
    selectConversation: (id: string) => Promise<void>
    sendMessage: (content: string) => Promise<void>
    addMessage: (msg: Message) => void
    updateMessageByClientId: (clientId: string, updater: (msg: Message) => Message) => void
    removeMessageByClientId: (clientId: string) => void
}

const supabaseConfigured = Boolean(import.meta.env.VITE_SUPABASE_URL) && Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY)
const API_URL = import.meta.env.VITE_API_URL || ''

const makeLocalId = () => {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
        return `local-${crypto.randomUUID()}`
    }
    return `local-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
}

const makeClientId = () => {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
        return crypto.randomUUID()
    }
    return `client-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
}

const truncateTitle = (content: string) => {
    const trimmed = content.trim().replace(/\s+/g, ' ')
    if (trimmed.length <= 64) return trimmed
    return `${trimmed.slice(0, 61)}...`
}

const notifyError = (message: string) => {
    console.error(message)
    if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('kb-toast', { detail: { type: 'error', message } }))
    }
}


export const useChatStore = create<ChatState>((set, get) => ({
    conversations: [],
    currentConversationId: null,
    messages: [],
    isLoading: false,

    fetchConversations: async () => {
        if (!supabaseConfigured) {
            set({ conversations: [], currentConversationId: null })
            return
        }

        set({ isLoading: true })
        try {
            const { data: authData, error: authError } = await supabase.auth.getUser()
            if (authError || !authData?.user) {
                set({ conversations: [], currentConversationId: null, messages: [] })
                return
            }

            const { data, error } = await supabase
                .from('conversations')
                .select('id, title, mode, settings, created_at, updated_at')
                .order('updated_at', { ascending: false })

            if (error) throw error

            const conversations = (data ?? []) as Conversation[]
            set(state => ({
                conversations,
                currentConversationId: state.currentConversationId ?? conversations[0]?.id ?? null,
            }))
        } catch (error) {
            console.error('Failed to fetch conversations:', error)
        } finally {
            set({ isLoading: false })
        }
    },

    selectConversation: async (id: string) => {
        if (!id) return
        const state = get()

        if (state.currentConversationId === id && (state.isLoading || state.messages.length > 0)) {
            return
        }

        set({ currentConversationId: id, messages: [], isLoading: true })

        if (!supabaseConfigured) {
            set({ isLoading: false })
            return
        }

        try {
            const { data, error } = await supabase
                .from('messages')
                .select('id, role, content, attachments, metadata, created_at')
                .eq('conversation_id', id)
                .order('created_at', { ascending: true })

            if (error) throw error

            set({ messages: (data ?? []) as Message[] })
        } catch (error) {
            console.error('Failed to fetch messages:', error)
        } finally {
            set({ isLoading: false })
        }
    },

    addMessage: (msg: Message) => {
        set(state => {
            const existingIndex = state.messages.findIndex(item => {
                if (item.id === msg.id) return true
                if (item.clientGeneratedId && msg.clientGeneratedId && item.clientGeneratedId === msg.clientGeneratedId) {
                    return true
                }
                if (msg.metadata?.assistant_client_id && item.clientGeneratedId === msg.metadata.assistant_client_id) {
                    return true
                }
                if (item.metadata?.assistant_client_id && msg.clientGeneratedId && item.metadata.assistant_client_id === msg.clientGeneratedId) {
                    return true
                }
                if (item.serverMessageId && msg.id && item.serverMessageId === msg.id) return true
                if (msg.serverMessageId && item.id && msg.serverMessageId === item.id) return true
                if (msg.metadata?.client_id && item.id === msg.metadata.client_id) return true
                if (item.metadata?.client_id && item.metadata.client_id === msg.id) return true
                if (msg.metadata?.client_id && item.clientGeneratedId === msg.metadata.client_id) return true
                if (item.metadata?.client_id && msg.clientGeneratedId && item.metadata.client_id === msg.clientGeneratedId) return true
                if (item.metadata?.client_id && msg.metadata?.client_id && item.metadata.client_id === msg.metadata.client_id) {
                    return true
                }
                return false
            })

            if (existingIndex >= 0) {
                const updated = [...state.messages]
                updated[existingIndex] = { ...updated[existingIndex], ...msg }
                return { messages: updated }
            }

            return { messages: [...state.messages, msg] }
        })
    },

    updateMessageByClientId: (clientId: string, updater: (msg: Message) => Message) => {
        set(state => {
            let updated = false
            const messages = state.messages.map(message => {
                if (message.clientGeneratedId === clientId) {
                    updated = true
                    return updater(message)
                }
                return message
            })

            return updated ? { messages } : state
        })
    },

    removeMessageByClientId: (clientId: string) => {
        set(state => ({
            messages: state.messages.filter(message => message.clientGeneratedId !== clientId),
        }))
    },

    sendMessage: async (content: string) => {
        const trimmed = content.trim()
        if (!trimmed) return

        const now = new Date().toISOString()
        const localUserId = makeLocalId()
        let conversationId = get().currentConversationId
        let conversation = get().conversations.find(item => item.id === conversationId)

        set({ isLoading: true })

        if (!conversationId) {
            const title = truncateTitle(trimmed)
            if (supabaseConfigured) {
                try {
                    const { data: authData } = await supabase.auth.getUser()
                    if (authData?.user) {
                        const { data, error } = await supabase
                            .from('conversations')
                            .insert({
                                user_id: authData.user.id,
                                title,
                                mode: 'eli5',
                                settings: {},
                            })
                            .select('id, title, mode, settings, created_at, updated_at')
                            .single()

                        if (error) throw error

                        if (data) {
                            conversation = data as Conversation
                            conversationId = data.id
                            set(state => ({
                                conversations: [conversation as Conversation, ...state.conversations],
                                currentConversationId: conversationId,
                            }))
                        }
                    }
                } catch (error) {
                    console.error('Failed to create conversation:', error)
                }
            }

            if (!conversationId) {
                conversationId = makeLocalId()
                conversation = {
                    id: conversationId,
                    title,
                    mode: 'eli5',
                    settings: {},
                    created_at: now,
                    updated_at: now,
                }
                set(state => ({
                    conversations: [conversation as Conversation, ...state.conversations],
                    currentConversationId: conversationId,
                }))
            }
        }

        const optimisticUserMessage: Message = {
            id: localUserId,
            role: 'user',
            content: trimmed,
            metadata: { client_id: localUserId },
            created_at: now,
            clientGeneratedId: localUserId,
        }

        get().addMessage(optimisticUserMessage)
        set(state => ({
            conversations: state.conversations
                .map(item =>
                    item.id === conversationId
                        ? { ...item, title: item.title || truncateTitle(trimmed), updated_at: now }
                        : item
                )
                .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
        }))

        const assistantClientId = makeClientId()
        const assistantPlaceholder: Message = {
            id: makeLocalId(),
            role: 'assistant',
            content: '',
            created_at: new Date().toISOString(),
            clientGeneratedId: assistantClientId,
            isStreaming: true,
        }

        get().addMessage(assistantPlaceholder)

        try {
            const { data: { session } } = supabaseConfigured
                ? await supabase.auth.getSession()
                : { data: { session: null } as any }
            const headers: HeadersInit = {
                'Content-Type': 'application/json',
            }
            if (session?.access_token) {
                headers['Authorization'] = `Bearer ${session.access_token}`
            }

            const response = await fetch(`${API_URL}/api/messages`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    conversation_id: conversationId,
                    content: trimmed,
                    client_generated_id: localUserId,
                    assistant_client_id: assistantClientId,
                }),
            })

            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`)
            }

            if (!response.body) {
                throw new Error('Streaming not supported in this environment')
            }

            const contentType = response.headers.get('content-type')
            if (contentType && !contentType.includes('text/event-stream')) {
                throw new Error(`Unexpected content-type: ${contentType}`)
            }

            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let buffer = ''
            const READ_TIMEOUT_MS = 20000

            while (true) {
                const { value, done } = await Promise.race([
                    reader.read(),
                    new Promise<ReadableStreamReadResult<Uint8Array>>((_, reject) =>
                        setTimeout(() => reject(new Error('Stream read timed out')), READ_TIMEOUT_MS)
                    ),
                ])
                if (done) break

                buffer += decoder.decode(value, { stream: true })

                const { events, remainder } = splitSseEvents(buffer)
                buffer = remainder

                for (const eventBlock of events) {
                    const dataPayload = extractSseData(eventBlock)
                    if (!dataPayload) continue
                    if (dataPayload === '[DONE]') continue

                    let payload: any = null
                    try {
                        payload = JSON.parse(dataPayload)
                    } catch {
                        payload = { delta: dataPayload }
                    }

                    if (payload?.delta) {
                        get().updateMessageByClientId(assistantClientId, message => ({
                            ...message,
                            content: `${message.content}${payload.delta}`,
                        }))
                    }

                    const serverMessageId = payload?.assistant_message_id || payload?.message_id
                    if (serverMessageId) {
                        get().updateMessageByClientId(assistantClientId, message => ({
                            ...message,
                            serverMessageId,
                        }))
                    }

                    if (payload?.error) {
                        throw new Error(payload.error)
                    }
                }
            }

            get().updateMessageByClientId(assistantClientId, message => ({
                ...message,
                isStreaming: false,
            }))
        } catch (error: any) {
            get().removeMessageByClientId(assistantClientId)
            notifyError(error?.message || 'Failed to send message')

            get().addMessage({
                id: makeLocalId(),
                role: 'assistant',
                content: 'Message failed to send. Please try again.',
                created_at: new Date().toISOString(),
                error: error?.message || 'Failed to send message',
            })
        } finally {
            const stillStreaming = get().messages.some(message => message.isStreaming)
            set({ isLoading: stillStreaming })
        }
    },
}))
