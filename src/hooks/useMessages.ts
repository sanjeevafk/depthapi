import { useEffect, useRef } from 'react'
import { supabase } from '../lib/supabase'
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

export const useMessages = () => {
    const currentConversationId = useChatStore(state => state.currentConversationId)
    const selectConversation = useChatStore(state => state.selectConversation)
    const addMessage = useChatStore(state => state.addMessage)
    const loadedConversationId = useRef<string | null>(null)

    useEffect(() => {
        if (!currentConversationId || !supabaseConfigured) return
        if (loadedConversationId.current === currentConversationId) return
        loadedConversationId.current = currentConversationId
        void selectConversation(currentConversationId)
    }, [currentConversationId, selectConversation])

    useEffect(() => {
        if (!currentConversationId || !supabaseConfigured) return

        const channel = supabase
            .channel(`messages:${currentConversationId}`)
            .on(
                'postgres_changes',
                {
                    event: 'INSERT',
                    schema: 'public',
                    table: 'messages',
                    filter: `conversation_id=eq.${currentConversationId}`,
                },
                payload => {
                    addMessage(mapMessage(payload.new))
                }
            )
            .on(
                'postgres_changes',
                {
                    event: 'UPDATE',
                    schema: 'public',
                    table: 'messages',
                    filter: `conversation_id=eq.${currentConversationId}`,
                },
                payload => {
                    addMessage(mapMessage(payload.new))
                }
            )
            .subscribe()

        return () => {
            supabase.removeChannel(channel)
        }
    }, [currentConversationId, addMessage])
}
