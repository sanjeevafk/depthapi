import { useState } from 'react'
import { Send } from 'lucide-react'
import { useChatStore } from '../../stores/useChatStore'
import { isModeGated } from '../../lib/chatModes'

export default function ChatInput() {
    const [value, setValue] = useState('')
    const sendMessage = useChatStore(state => state.sendMessage)
    const isLoading = useChatStore(state => state.isLoading)
    const currentMode = useChatStore(state => state.currentMode)
    const isPro = useChatStore(state => state.isPro)
    const gatedModes = useChatStore(state => state.gatedModes)
    const openUpgradeModal = useChatStore(state => state.openUpgradeModal)

    const isGated = isModeGated(currentMode, isPro, gatedModes)
    const isSendDisabled = isLoading || (!value.trim() && !isGated)

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

    return (
        <div className="border-t border-white/5 bg-dark-800 px-6 py-4">
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
            <p className="text-xs text-gray-500 mt-2">Shift + Enter for a new line.</p>
        </div>
    )
}
