import React, { useEffect, useId, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Lock, X } from 'lucide-react'
import { useChatStore } from '../stores/useChatStore'
import { CHAT_MODE_OPTIONS, isModeGated } from '../lib/chatModes'
import { notifyToast } from '../lib/toast'

interface UpgradeModalProps {
    isOpen: boolean
    onClose: () => void
    onUpgrade?: () => void
    onUseByok?: () => void
}

export const UpgradeModal: React.FC<UpgradeModalProps> = ({ isOpen, onClose, onUpgrade, onUseByok }) => {
    const currentMode = useChatStore(state => state.currentMode)
    const setMode = useChatStore(state => state.setMode)
    const isPro = useChatStore(state => state.isPro)
    const gatedModes = useChatStore(state => state.gatedModes)
    const modalId = useId()
    const descriptionId = useId()
    const modalRef = useRef<HTMLDivElement>(null)

    const handleSelect = (modeId: typeof currentMode) => {
        if (modeId === currentMode) return
        if (isModeGated(modeId, isPro, gatedModes)) return
        setMode(modeId)
        onClose()
    }

    const handleUpgrade = () => {
        if (onUpgrade) {
            onUpgrade()
            return
        }
        notifyToast('Upgrade flow not configured yet.', 'info')
    }

    const handleUseByok = () => {
        if (onUseByok) {
            onUseByok()
            return
        }
        notifyToast('BYOK flow not configured yet.', 'info')
    }

    // Only shift focus when the modal first opens
    useEffect(() => {
        if (!isOpen) return
        modalRef.current?.focus()
    }, [isOpen])

    // Escape key — depends on onClose so kept separate
    useEffect(() => {
        if (!isOpen) return
        const handleKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') onClose()
        }
        window.addEventListener('keydown', handleKey)
        return () => window.removeEventListener('keydown', handleKey)
    }, [isOpen, onClose])

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    onClick={event => {
                        if (event.target === event.currentTarget) {
                            onClose()
                        }
                    }}
                >
                    <motion.div
                        ref={modalRef}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby={modalId}
                        aria-describedby={descriptionId}
                        tabIndex={-1}
                        className="relative w-full max-w-md rounded-2xl border border-white/10 bg-dark-900 p-6 shadow-2xl"
                        initial={{ scale: 0.96, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0.98, opacity: 0 }}
                        transition={{ type: 'spring', stiffness: 220, damping: 22 }}
                        onClick={event => event.stopPropagation()}
                    >
                        <button
                            type="button"
                            onClick={onClose}
                            className="absolute right-4 top-4 rounded-full p-2 text-gray-400 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                            aria-label="Close modal"
                        >
                            <X className="h-4 w-4" />
                        </button>
                        <div className="text-sm uppercase tracking-[0.3em] text-gray-500">Pro Mode</div>
                        <h2 id={modalId} className="mt-2 text-xl font-semibold text-white">
                            This mode requires Pro.
                        </h2>
                        <p id={descriptionId} className="mt-2 text-sm text-gray-400">
                            Upgrade or use your own key?
                        </p>

                        <div className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                            {CHAT_MODE_OPTIONS.map(mode => {
                                const isActive = currentMode === mode.id
                                const isGated = isModeGated(mode.id, isPro, gatedModes)
                                return (
                                    <label
                                        key={mode.id}
                                        className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition ${
                                            isActive
                                                ? 'border-blue-500/60 bg-blue-500/10 text-white'
                                                : 'border-white/10 bg-white/5 text-gray-300'
                                        } ${isGated ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
                                    >
                                        <input
                                            type="radio"
                                            name="chat-mode"
                                            checked={isActive}
                                            onChange={() => handleSelect(mode.id)}
                                            disabled={isGated}
                                            className="h-4 w-4 accent-blue-500"
                                        />
                                        <span className="flex flex-1 flex-col">
                                            <span className="flex items-center gap-2 font-semibold">
                                                {mode.label}
                                                {isGated && <Lock className="h-3 w-3 text-yellow-400" />}
                                            </span>
                                            <span className="text-xs text-gray-400">
                                                {mode.description}
                                            </span>
                                        </span>
                                    </label>
                                )
                            })}
                        </div>

                        <div className="mt-6 flex flex-col gap-2">
                            <button
                                type="button"
                                onClick={handleUpgrade}
                                className="w-full rounded-xl bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 transition"
                            >
                                Upgrade
                            </button>
                            <button
                                type="button"
                                onClick={handleUseByok}
                                className="w-full rounded-xl border border-white/10 bg-white/5 py-2.5 text-sm font-semibold text-white hover:border-white/20 hover:bg-white/10 transition"
                            >
                                Use BYOK
                            </button>
                            <button
                                type="button"
                                onClick={onClose}
                                className="w-full rounded-xl py-2.5 text-sm text-gray-400 hover:text-white transition"
                            >
                                Cancel
                            </button>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    )
}
