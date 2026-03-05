import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { useQuery } from '@tanstack/react-query'
import { Bookmark, LogIn, LogOut, Search, User as UserIcon } from 'lucide-react'
import { getPinnedTopics } from '../../api'
import { useAuth } from '../../context/AuthContext'
import { useConversations } from '../../hooks/useConversations'
import { useChatStore } from '../../stores/useChatStore'
import { CHAT_MODE_STYLES, formatModeLabel } from '../../lib/chatModes'

export default function ConversationList() {
    const { user, profile, signOut, signInWithGoogle } = useAuth()
    const { conversations, lastMessageByConversationId, isLoading } = useConversations()
    const currentConversationId = useChatStore(state => state.currentConversationId)
    const selectConversation = useChatStore(state => state.selectConversation)
    const sendMessage = useChatStore(state => state.sendMessage)
    const lastActiveMessage = useChatStore(state => {
        const lastId = state.messageIds[state.messageIds.length - 1]
        return lastId ? state.messagesById[lastId] : undefined
    })
    const [searchValue, setSearchValue] = useState('')

    const pinnedQuery = useQuery({
        queryKey: ['pinned-topics'],
        queryFn: getPinnedTopics,
        staleTime: 60_000,
    })

    const pinnedTopics = pinnedQuery.data ?? []

    const filteredConversations = useMemo(() => {
        const term = searchValue.trim().toLowerCase()
        if (!term) return conversations

        return conversations.filter(conversation => {
            const isActive = conversation.id === currentConversationId
            const activePreview = isActive ? lastActiveMessage?.content : undefined
            const preview = activePreview || lastMessageByConversationId[conversation.id]?.content || ''
            const title = conversation.title || ''
            return title.toLowerCase().includes(term) || preview.toLowerCase().includes(term)
        })
    }, [conversations, currentConversationId, lastActiveMessage, lastMessageByConversationId, searchValue])

    return (
        <aside className="w-full md:w-80 md:shrink-0 border-r border-white/5 bg-dark-800 flex flex-col border-b md:border-b-0">
            <div className="px-4 py-4 border-b border-white/5">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <img src="/favicon.svg" alt="Logo" className="w-8 h-8 drop-shadow-[0_0_8px_rgba(6,182,212,0.5)]" />
                        <div className="flex flex-col">
                            <span className="text-lg font-black tracking-tight leading-none">Know<span className="text-cyan-500">Bear</span></span>
                            <span className="text-[10px] font-mono text-gray-500">Chat Studio</span>
                        </div>
                    </div>
                    {isLoading && (
                        <span className="text-xs text-gray-500 animate-pulse">Syncing...</span>
                    )}
                </div>

                <div className="mt-4">
                    <label className="relative block">
                        <span className="sr-only">Search conversations</span>
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
                        <input
                            value={searchValue}
                            onChange={event => setSearchValue(event.target.value)}
                            placeholder="Search conversations"
                            className="w-full rounded-xl bg-dark-900/60 border border-white/10 pl-9 pr-3 py-2 text-xs text-gray-200 placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-cyan-500/30"
                        />
                    </label>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto max-h-56 md:max-h-none">
                {pinnedTopics.length > 0 && (
                    <div className="px-4 pt-4">
                        <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.25em] text-gray-500">
                            <Bookmark className="h-3 w-3" />
                            Pinned
                        </div>
                        <div className="mt-3 grid grid-cols-1 gap-2">
                            {pinnedTopics.slice(0, 4).map(topic => (
                                <button
                                    key={topic.id}
                                    onClick={() => void sendMessage(topic.title)}
                                    className="w-full rounded-xl border border-white/5 bg-dark-900/40 px-3 py-2 text-left text-xs text-gray-200 hover:border-cyan-500/40 hover:text-white transition"
                                >
                                    <div className="font-semibold text-sm text-gray-100 truncate">{topic.title}</div>
                                    <div className="text-[11px] text-gray-500 line-clamp-2">{topic.description}</div>
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                <div className="px-4 pt-5 pb-3">
                    <div className="flex items-center justify-between">
                        <h2 className="text-xs uppercase tracking-[0.3em] text-gray-500">Conversations</h2>
                        <span className="text-[10px] text-gray-600">{filteredConversations.length}</span>
                    </div>
                </div>

                {filteredConversations.length === 0 ? (
                    <div className="px-4 pb-8 text-sm text-gray-500">No conversations yet.</div>
                ) : (
                    <div className="space-y-2 px-3 pb-6">
                        {filteredConversations.map(conversation => {
                            const isActive = conversation.id === currentConversationId
                            const activePreview = isActive ? lastActiveMessage?.content : undefined
                            const preview = activePreview || lastMessageByConversationId[conversation.id]?.content || 'No messages yet'
                            return (
                                <motion.button
                                    key={conversation.id}
                                    whileHover={{ scale: 1.01 }}
                                    whileTap={{ scale: 0.99 }}
                                    onClick={() => selectConversation(conversation.id)}
                                    className={`w-full text-left rounded-xl px-3 py-3 transition border ${
                                        isActive
                                            ? 'bg-dark-700 border-white/10 shadow-lg'
                                            : 'bg-dark-900/40 border-white/5 hover:border-white/10'
                                    }`}
                                >
                                    <div className="flex items-center justify-between gap-2">
                                        <h3 className="text-sm font-semibold text-gray-100 truncate">
                                            {conversation.title || 'Untitled conversation'}
                                        </h3>
                                        <span
                                            className={`text-[10px] uppercase tracking-wide px-2 py-0.5 rounded-full ${
                                                CHAT_MODE_STYLES[conversation.mode] || 'bg-white/5 text-gray-300 border border-white/10'
                                            }`}
                                        >
                                            {formatModeLabel(conversation.mode)}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-500 mt-2 truncate">
                                        {preview}
                                    </p>
                                </motion.button>
                            )
                        })}
                    </div>
                )}
            </div>

            <div className="border-t border-white/5 px-4 py-4">
                {user ? (
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="w-8 h-8 rounded-full bg-cyan-600 flex items-center justify-center text-white overflow-hidden ring-2 ring-cyan-500/20">
                                {user.user_metadata.avatar_url ? (
                                    <img src={user.user_metadata.avatar_url} alt="Avatar" className="w-full h-full object-cover" />
                                ) : (
                                    <UserIcon size={16} />
                                )}
                            </div>
                            <div className="flex flex-col">
                                <span className="text-sm text-white font-medium truncate max-w-[140px]">
                                    {user.user_metadata.full_name || user.email?.split('@')[0]}
                                </span>
                                <span className="text-[10px] text-gray-500">
                                    {profile?.is_pro ? 'Pro plan' : 'Free plan'}
                                </span>
                            </div>
                        </div>
                        <button
                            onClick={() => signOut()}
                            className="text-gray-400 hover:text-white transition-colors"
                            title="Sign out"
                        >
                            <LogOut size={16} />
                        </button>
                    </div>
                ) : (
                    <button
                        onClick={signInWithGoogle}
                        className="w-full flex items-center justify-center gap-2 rounded-xl border border-white/10 bg-dark-900/60 py-2 text-xs text-gray-200 hover:border-cyan-500/40 hover:text-white transition"
                    >
                        <LogIn size={14} /> Sign in to sync
                    </button>
                )}
            </div>
        </aside>
    )
}
