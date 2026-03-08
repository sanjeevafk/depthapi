import { useEffect, useMemo, useState } from 'react'
import { Lock, RefreshCcw, X } from 'lucide-react'
import { CHAT_MODE_OPTIONS, isModeGated } from '../../lib/chatModes'
import { useChatStore } from '../../stores/useChatStore'

export default function RegenerationModal(): JSX.Element | null {
    const isOpen = useChatStore(state => state.regenerationModalOpen)
    const targetId = useChatStore(state => state.regenerationTargetId)
    const messageIds = useChatStore(state => state.messageIds)
    const messagesById = useChatStore(state => state.messagesById)
    const currentMode = useChatStore(state => state.currentMode)
    const isPro = useChatStore(state => state.isPro)
    const gatedModes = useChatStore(state => state.gatedModes)
    const regenerateMessage = useChatStore(state => state.regenerateMessage)
    const closeRegenerationModal = useChatStore(state => state.closeRegenerationModal)
    const openUpgradeModal = useChatStore(state => state.openUpgradeModal)

    const [selectedMode, setSelectedMode] = useState(currentMode)

    useEffect(() => {
        if (isOpen) {
            setSelectedMode(currentMode)
        }
    }, [isOpen, currentMode])

    const userPrompt = useMemo(() => {
        if (!targetId) return ''
        const targetIndex = messageIds.indexOf(targetId)
        if (targetIndex < 0) return ''
        for (let i = targetIndex - 1; i >= 0; i -= 1) {
            const candidate = messagesById[messageIds[i]]
            if (candidate?.role === 'user') return candidate.content
        }
        return ''
    }, [messageIds, messagesById, targetId])

    if (!isOpen) return null

    const gated = isModeGated(selectedMode, isPro, gatedModes)

    const handleConfirm = async () => {
        if (!targetId) return
        if (gated) {
            openUpgradeModal()
            return
        }
        await regenerateMessage(targetId, selectedMode)
        closeRegenerationModal()
    }

    return (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={closeRegenerationModal} />
            <div className="relative w-full max-w-lg rounded-2xl border border-white/10 bg-dark-800 p-6 shadow-2xl">
                <div className="flex items-start justify-between">
                    <div>
                        <h3 className="text-lg font-semibold text-white">Regenerate Response</h3>
                        <p className="text-sm text-gray-400 mt-1">Pick a response style and rerun the assistant.</p>
                    </div>
                    <button
                        onClick={closeRegenerationModal}
                        className="text-gray-500 hover:text-white transition"
                        aria-label="Close"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                {userPrompt && (
                    <div className="mt-4 rounded-xl border border-white/10 bg-dark-900/60 p-3 text-xs text-gray-300">
                        <div className="text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2">Original Prompt</div>
                        <p className="line-clamp-3">{userPrompt}</p>
                    </div>
                )}

                <div className="mt-5 grid grid-cols-1 gap-2 max-h-64 overflow-y-auto">
                    {CHAT_MODE_OPTIONS.map(option => {
                        const isActive = selectedMode === option.id
                        const isLocked = isModeGated(option.id, isPro, gatedModes)
                        return (
                            <button
                                key={option.id}
                                type="button"
                                onClick={() => setSelectedMode(option.id)}
                                className={`flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left text-sm transition ${
                                    isActive
                                        ? 'border-cyan-500/40 bg-cyan-500/10 text-white'
                                        : 'border-white/5 bg-dark-900/40 text-gray-300 hover:border-white/10'
                                }`}
                            >
                                <div className="flex items-center gap-2">
                                    <span className="font-semibold">{option.label}</span>
                                    {isLocked && <Lock className="h-3 w-3 text-yellow-400" />}
                                </div>
                                <span className="text-xs text-gray-500">{option.description}</span>
                            </button>
                        )
                    })}
                </div>

                <div className="mt-6 flex items-center justify-end gap-2">
                    <button
                        onClick={closeRegenerationModal}
                        className="px-4 py-2 text-sm text-gray-400 hover:text-white"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => void handleConfirm()}
                        className="px-4 py-2 rounded-xl text-sm font-semibold flex items-center gap-2 bg-cyan-600 text-white hover:bg-cyan-500"
                    >
                        <RefreshCcw className="h-4 w-4" />
                        {gated ? 'Upgrade to Unlock' : 'Regenerate'}
                    </button>
                </div>
            </div>
        </div>
    )
}
