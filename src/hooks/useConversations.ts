import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'
import { useChatStore } from '../stores/useChatStore'
import type { Message } from '../types/chat'

const supabaseConfigured = Boolean(import.meta.env.VITE_SUPABASE_URL) && Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY)

const mapMessage = (record: any): Message => ({
    id: record.id,
    role: record.role,
    content: record.content,
    attachments: record.attachments ?? undefined,
    metadata: record.metadata ?? undefined,
    created_at: record.created_at,
})

export const useConversations = () => {
    const { user } = useAuth()
    const conversations = useChatStore(state => state.conversations)
    const fetchConversations = useChatStore(state => state.fetchConversations)
    const [lastMessageByConversationId, setLastMessageByConversationId] = useState<Record<string, Message | null>>({})

    useEffect(() => {
        if (!user || !supabaseConfigured) return
        fetchConversations()
    }, [user?.id, fetchConversations])

    useEffect(() => {
        if (!supabaseConfigured) return
        if (conversations.length === 0) {
            setLastMessageByConversationId({})
            return
        }

        const conversationIds = conversations.map(item => item.id)
        let isMounted = true

        const loadLastMessages = async () => {
            try {
                const { data, error } = await supabase
                    .from('messages')
                    .select('id, conversation_id, role, content, attachments, metadata, created_at')
                    .in('conversation_id', conversationIds)
                    .order('created_at', { ascending: false })

                if (error) throw error

                const next: Record<string, Message | null> = {}
                for (const message of data ?? []) {
                    if (!next[message.conversation_id]) {
                        next[message.conversation_id] = mapMessage(message)
                    }
                }

                if (isMounted) setLastMessageByConversationId(next)
            } catch (error) {
                console.error('Failed to load last messages:', error)
            }
        }

        loadLastMessages()

        return () => {
            isMounted = false
        }
    }, [conversations])

    useEffect(() => {
        if (!user || !supabaseConfigured) return

        const channel = supabase
            .channel(`conversations:${user.id}`)
            .on(
                'postgres_changes',
                { event: '*', schema: 'public', table: 'conversations', filter: `user_id=eq.${user.id}` },
                () => {
                    fetchConversations()
                }
            )
            .subscribe()

        return () => {
            supabase.removeChannel(channel)
        }
    }, [user?.id, fetchConversations])

    return {
        conversations,
        lastMessageByConversationId,
    }
}
