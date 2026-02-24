import { motion } from 'framer-motion'
import { useConversations } from '../../hooks/useConversations'
import { useChatStore } from '../../stores/useChatStore'

const modeStyles: Record<string, string> = {
    eli5: 'bg-green-500/10 text-green-300 border border-green-500/20',
    ensemble: 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/20',
    technical: 'bg-blue-500/10 text-blue-300 border border-blue-500/20',
    socratic: 'bg-purple-500/10 text-purple-300 border border-purple-500/20',
}

export default function ConversationList() {
    const { conversations, lastMessageByConversationId } = useConversations()
    const currentConversationId = useChatStore(state => state.currentConversationId)
    const selectConversation = useChatStore(state => state.selectConversation)
    const isLoading = useChatStore(state => state.isLoading)
    const messages = useChatStore(state => state.messages)

    return (
        <aside className="w-full md:w-80 md:shrink-0 border-r border-white/5 bg-dark-800 flex flex-col border-b md:border-b-0">
            <div className="px-4 py-4 border-b border-white/5">
                <div className="flex items-center justify-between">
                    <h2 className="text-sm uppercase tracking-[0.2em] text-gray-500">Conversations</h2>
                    {isLoading && (
                        <span className="text-xs text-gray-500 animate-pulse">Syncing...</span>
                    )}
                </div>
            </div>

            <div className="flex-1 overflow-y-auto max-h-56 md:max-h-none">
                {conversations.length === 0 ? (
                    <div className="px-4 py-8 text-sm text-gray-500">No conversations yet.</div>
                ) : (
                    <div className="space-y-2 p-3">
                        {conversations.map(conversation => {
                            const isActive = conversation.id === currentConversationId
                            const activePreview = isActive ? messages[messages.length - 1]?.content : undefined
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
                                                modeStyles[conversation.mode] || 'bg-white/5 text-gray-300 border border-white/10'
                                            }`}
                                        >
                                            {conversation.mode}
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
        </aside>
    )
}
