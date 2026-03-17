import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'
import { useChatStore } from '../stores/useChatStore'
import type { Conversation, Message } from '../types/chat'

const supabaseConfigured = Boolean(import.meta.env.VITE_SUPABASE_URL) && Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY)

type MessageRecord = {
    id: string
    role: Message['role']
    content: string
    attachments?: Message['attachments']
    metadata?: Message['metadata']
    created_at: string
    conversation_id?: string
}

const mapMessage = (record: MessageRecord): Message => ({
    id: record.id,
    role: record.role,
    content: record.content,
    attachments: record.attachments ?? undefined,
    metadata: record.metadata ?? undefined,
    created_at: record.created_at,
})

const fetchConversations = async (): Promise<Conversation[]> => {
    const { data, error } = await supabase
        .from('conversations')
        .select('id, title, mode, settings, created_at, updated_at')
        .order('updated_at', { ascending: false })

    if (error) throw error
    return (data ?? []) as Conversation[]
}

const fetchLastMessages = async (conversationIds: string[]): Promise<Record<string, Message | null>> => {
    if (conversationIds.length === 0) return {}

    const { data, error } = await supabase
        .from('messages')
        .select('id, conversation_id, role, content, attachments, metadata, created_at')
        .in('conversation_id', conversationIds)
        .order('created_at', { ascending: false })

    if (error) throw error

    const next: Record<string, Message | null> = {}
    for (const message of data ?? []) {
        if (message.conversation_id && !next[message.conversation_id]) {
            next[message.conversation_id] = mapMessage(message)
        }
    }

    return next
}

interface UseConversationsResult {
    conversations: Conversation[]
    lastMessageByConversationId: Record<string, Message | null>
    isLoading: boolean
}

export function useConversations(): UseConversationsResult {
    const { user } = useAuth()
    const syncConversations = useChatStore(state => state.syncConversations)
    const conversations = useChatStore(state => state.conversations)
    const queryClient = useQueryClient()

    const conversationsQuery = useQuery({
        queryKey: ['conversations', user?.id],
        enabled: Boolean(user && supabaseConfigured),
        queryFn: fetchConversations,
        staleTime: 30_000,
    })

    useEffect(() => {
        if (!user || !supabaseConfigured) {
            syncConversations([])
            return
        }
        if (conversationsQuery.data) {
            syncConversations(conversationsQuery.data)
        }
    }, [user, syncConversations, conversationsQuery.data])

    const conversationIds = useMemo(
        () => (conversationsQuery.data ?? conversations).map(item => item.id),
        [conversations, conversationsQuery.data]
    )

    const lastMessagesQuery = useQuery({
        queryKey: ['conversation-last-messages', conversationIds],
        enabled: supabaseConfigured && conversationIds.length > 0,
        queryFn: () => fetchLastMessages(conversationIds),
        staleTime: 15_000,
    })

    useEffect(() => {
        if (!user || !supabaseConfigured) return

        const channel = supabase
            .channel(`conversations:${user.id}`)
            .on(
                'postgres_changes',
                { event: '*', schema: 'public', table: 'conversations', filter: `user_id=eq.${user.id}` },
                () => {
                    queryClient.invalidateQueries({ queryKey: ['conversations', user.id] })
                }
            )
            .subscribe()

        return () => {
            supabase.removeChannel(channel)
        }
    }, [user, queryClient])

    return {
        conversations,
        lastMessageByConversationId: lastMessagesQuery.data ?? {},
        isLoading: conversationsQuery.isLoading,
    }
}
