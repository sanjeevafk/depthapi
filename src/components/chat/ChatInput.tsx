import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Send } from 'lucide-react'
import { useChatStore } from '../../stores/useChatStore'
import { CHAT_MODE_OPTIONS, formatModeLabel, isModeGated } from '../../lib/chatModes'

export default function ChatInput() {
    const [value, setValue] = useState('')
    const [modeOpen, setModeOpen] = useState(false)
    const dropdownRef = useRef<HTMLDivElement>(null)
    const sendMessage = useChatStore(state => state.sendMessage)
    const isLoading = useChatStore(state => state.isLoading)
    const currentMode = useChatStore(state => state.currentMode)
    const isPro = useChatStore(state => state.isPro)
    const gatedModes = useChatStore(state => state.gatedModes)
    const setMode = useChatStore(state => state.setMode)
    const openUpgradeModal = useChatStore(state => state.openUpgradeModal)
    const isStreaming = useChatStore(state => state.messageIds.some(id => state.messagesById[id]?.isStreaming))

    const isGated = isModeGated(currentMode, isPro, gatedModes)
    const isSendDisabled = isLoading || (!value.trim() && !isGated)

    const modeOptions = useMemo(() => CHAT_MODE_OPTIONS, [])

    useEffect(() => {
        const handler = (event: MouseEvent) => {
            if (!dropdownRef.current) return
            if (dropdownRef.current.contains(event.target as Node)) return
            setModeOpen(false)
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    const handleSend = async () => {
        if (isLoading) return
        if (isGated) {
            openUpgradeModal()
            return
        }
        if (!value.trim()) return
        const content = value
        setValue('')
        await sendMessage(content)
    }

    const handleModeSelect = (modeId: typeof currentMode) => {
        if (isModeGated(modeId, isPro, gatedModes)) {
            openUpgradeModal()
            return
        }
        setMode(modeId)
        setModeOpen(false)
    }

    return (
        <div className="sticky bottom-0 px-6 pb-6 bg-gradient-to-t from-dark-900 via-dark-900/80 to-transparent">
            <div className="mx-auto max-w-4xl">
                <div className="rounded-2xl border border-white/10 bg-dark-800/90 backdrop-blur-xl p-4 shadow-[0_20px_60px_rgba(0,0,0,0.45)]">
                    {isStreaming ? (
                        <div className="space-y-3">
                            <div className="h-4 w-3/4 rounded-full bg-white/5 relative overflow-hidden">
                                <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/10 to-transparent" />
                            </div>
                            <div className="h-4 w-1/2 rounded-full bg-white/5 relative overflow-hidden">
                                <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/10 to-transparent" />
                            </div>
                            <div className="flex items-center gap-2 text-xs text-gray-500">
                                <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
                                Thinking...
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-end gap-3">
                            <textarea
                                value={value}
                                onChange={event => setValue(event.target.value)}
                                onKeyDown={event => {
                                    if (event.key === 'Enter' && !event.shiftKey) {
                                        event.preventDefault()
                                        void handleSend()
                                    }
                                }}
                                placeholder="Ask KnowBear anything..."
                                className="flex-1 resize-none rounded-2xl bg-dark-900 border border-white/10 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-accent-primary/40 min-h-[52px] max-h-40"
                                rows={2}
                            />
                            <div className="relative" ref={dropdownRef}>
                                <button
                                    type="button"
                                    onClick={() => setModeOpen(value => !value)}
                                    className="h-12 rounded-2xl border border-white/10 bg-dark-900/60 px-3 text-xs text-gray-200 flex items-center gap-2 hover:border-white/20"
                                >
                                    <span className="font-semibold">{formatModeLabel(currentMode)}</span>
                                    <ChevronDown className={`h-4 w-4 text-gray-400 transition ${modeOpen ? 'rotate-180' : ''}`} />
                                </button>
                                {modeOpen && (
                                    <div className="absolute bottom-full right-0 mb-2 w-48 rounded-2xl border border-white/10 bg-dark-900/95 p-2 shadow-2xl">
                                        {modeOptions.map(option => {
                                            const gated = isModeGated(option.id, isPro, gatedModes)
                                            return (
                                                <button
                                                    key={option.id}
                                                    onClick={() => handleModeSelect(option.id)}
                                                    className={`w-full text-left rounded-xl px-3 py-2 text-xs transition ${
                                                        option.id === currentMode
                                                            ? 'bg-cyan-500/15 text-white'
                                                            : 'text-gray-300 hover:bg-white/5'
                                                    } ${gated ? 'opacity-60 cursor-not-allowed' : ''}`}
                                                >
                                                    <div className="flex items-center justify-between">
                                                        <span className="font-semibold">{option.label}</span>
                                                        {gated && <span className="text-[10px] text-yellow-400">PRO</span>}
                                                    </div>
                                                    <span className="text-[10px] text-gray-500">{option.description}</span>
                                                </button>
                                            )
                                        })}
                                    </div>
                                )}
                            </div>
                            <button
                                onClick={() => void handleSend()}
                                aria-disabled={isSendDisabled}
                                title={isGated ? 'This mode requires Pro.' : undefined}
                                className={`h-12 w-12 rounded-2xl flex items-center justify-center transition ${
                                    isGated
                                        ? 'bg-white/10 text-gray-400 cursor-not-allowed'
                                        : 'bg-accent-primary text-white hover:bg-accent-primary/90'
                                } ${isSendDisabled ? 'opacity-50 cursor-not-allowed' : ''}`}
                                aria-label="Send message"
                            >
                                {isLoading ? (
                                    <span className="h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
                                ) : (
                                    <Send className="w-4 h-4" />
                                )}
                            </button>
                        </div>
                    )}
                </div>
                {!isStreaming && <p className="text-xs text-gray-500 mt-2">Shift + Enter for a new line.</p>}
            </div>
        </div>
    )
}
