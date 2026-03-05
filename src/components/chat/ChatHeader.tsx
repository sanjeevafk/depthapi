import { useEffect, useState } from 'react'
import { Check, Pencil, X } from 'lucide-react'
import { useChatStore } from '../../stores/useChatStore'
import { CHAT_MODE_STYLES, formatModeLabel } from '../../lib/chatModes'

export default function ChatHeader() {
    const conversations = useChatStore(state => state.conversations)
    const currentConversationId = useChatStore(state => state.currentConversationId)
    const currentMode = useChatStore(state => state.currentMode)
    const currentPromptMode = useChatStore(state => state.currentPromptMode)
    const isLoading = useChatStore(state => state.isLoading)
    const renameConversation = useChatStore(state => state.renameConversation)

    const conversation = conversations.find(item => item.id === currentConversationId)
    const [isEditing, setIsEditing] = useState(false)
    const [draftTitle, setDraftTitle] = useState(conversation?.title || '')

    useEffect(() => {
        setDraftTitle(conversation?.title || '')
        setIsEditing(false)
    }, [conversation?.id, conversation?.title])

    const handleSave = async () => {
        if (!currentConversationId) return
        const nextTitle = draftTitle.trim()
        if (!nextTitle) {
            setDraftTitle(conversation?.title || '')
            setIsEditing(false)
            return
        }
        try {
            await renameConversation(currentConversationId, nextTitle)
            setIsEditing(false)
        } catch (error) {
            console.error('Failed to rename conversation:', error)
            // Optionally show a toast/notification to the user
        }
    }

    return (
        <div className="border-b border-white/5 bg-dark-800/80 px-6 py-4 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3">
                <div className="flex-1 min-w-0">
                    {isEditing ? (
                        <input
                            value={draftTitle}
                            onChange={event => setDraftTitle(event.target.value)}
                            onKeyDown={event => {
                                if (event.key === 'Enter') {
                                    event.preventDefault()
                                    void handleSave()
                                }
                                if (event.key === 'Escape') {
                                    event.preventDefault()
                                    setDraftTitle(conversation?.title || '')
                                    setIsEditing(false)
                                }
                            }}
                            autoFocus
                            className="w-full bg-dark-900/60 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-cyan-500/30"
                            placeholder="Untitled conversation"
                        />
                    ) : (
                        <div className="flex items-center gap-2">
                            <h1 className="text-lg font-semibold text-white truncate">
                                {conversation?.title || 'Untitled conversation'}
                            </h1>
                            {currentConversationId && (
                                <button
                                    onClick={() => setIsEditing(true)}
                                    className="text-gray-500 hover:text-cyan-300 transition"
                                    title="Edit title"
                                >
                                    <Pencil className="h-4 w-4" />
                                </button>
                            )}
                        </div>
                    )}
                </div>

                {isEditing && (
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => void handleSave()}
                            className="h-8 w-8 rounded-lg border border-white/10 bg-dark-900/60 flex items-center justify-center text-cyan-300 hover:text-cyan-200"
                            title="Save"
                        >
                            <Check className="h-4 w-4" />
                        </button>
                        <button
                            onClick={() => {
                                setDraftTitle(conversation?.title || '')
                                setIsEditing(false)
                            }}
                            className="h-8 w-8 rounded-lg border border-white/10 bg-dark-900/60 flex items-center justify-center text-gray-400 hover:text-white"
                            title="Cancel"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </div>
                )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <span className={`text-[10px] uppercase tracking-wide px-2 py-1 rounded-full ${CHAT_MODE_STYLES[currentMode] || 'bg-white/5 text-gray-300 border border-white/10'}`}>
                    Mode: {formatModeLabel(currentMode)}
                </span>
                <span className="text-[10px] uppercase tracking-wide px-2 py-1 rounded-full bg-white/5 text-gray-300 border border-white/10">
                    Prompt: {formatModeLabel(currentPromptMode)}
                </span>
                {isLoading && (
                    <span className="text-[10px] uppercase tracking-wide px-2 py-1 rounded-full bg-cyan-500/10 text-cyan-200 border border-cyan-500/20">
                        Streaming
                    </span>
                )}
            </div>
        </div>
    )
}
