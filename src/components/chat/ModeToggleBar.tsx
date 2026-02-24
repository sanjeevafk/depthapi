import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import {
    BookOpen,
    GraduationCap,
    LibraryBig,
    School,
    Laugh,
    Brain,
    HelpCircle,
    Crown,
    HeartHandshake,
    Sparkles,
    Lock,
    ChevronDown,
} from 'lucide-react'
import { useChatStore } from '../../stores/useChatStore'
import type { ChatMode } from '../../types/chat'
import {
    CHAT_MODE_ACCENTS,
    CHAT_MODE_OPTIONS,
    CHAT_DROPDOWN_MODES,
    CHAT_QUICK_MODES,
    isModeGated,
    isPromptMode,
} from '../../lib/chatModes'

const MODE_ICONS: Record<ChatMode, typeof BookOpen> = {
    eli5: BookOpen,
    eli10: School,
    eli12: LibraryBig,
    eli15: GraduationCap,
    'meme-style': Laugh,
    classic60: Sparkles,
    gentle70: HeartHandshake,
    warm80: Crown,
    ensemble: Sparkles,
    'technical-depth': Brain,
    socratic: HelpCircle,
}

export default function ModeToggleBar() {
    const currentMode = useChatStore(state => state.currentMode)
    const currentPromptMode = useChatStore(state => state.currentPromptMode)
    const setMode = useChatStore(state => state.setMode)
    const setPromptMode = useChatStore(state => state.setPromptMode)
    const isPro = useChatStore(state => state.isPro)
    const gatedModes = useChatStore(state => state.gatedModes)
    const openUpgradeModal = useChatStore(state => state.openUpgradeModal)
    const [dropdownOpen, setDropdownOpen] = useState(false)
    const [activeIndex, setActiveIndex] = useState<number>(-1)
    const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
    const pendingFocusIndex = useRef<number | null>(null)
    const dropdownRef = useRef<HTMLDivElement>(null)
    const triggerRef = useRef<HTMLButtonElement>(null)
    const listboxId = useId()

    const handleSelect = (mode: ChatMode) => {
        if (mode === currentMode) return
        if (isModeGated(mode, isPro, gatedModes)) {
            openUpgradeModal()
            return
        }
        setMode(mode)
    }

    const dropdownOptions = useMemo(
        () => CHAT_MODE_OPTIONS.filter(mode => CHAT_DROPDOWN_MODES.includes(mode.id)),
        []
    )
    const quickOptions = useMemo(
        () => CHAT_MODE_OPTIONS.filter(mode => CHAT_QUICK_MODES.includes(mode.id)),
        []
    )
    const dropdownActive = dropdownOptions.find(option => option.id === currentPromptMode)

    const closeDropdown = (returnFocus: boolean) => {
        setDropdownOpen(false)
        if (returnFocus) {
            requestAnimationFrame(() => triggerRef.current?.focus())
        }
    }

    const focusOptionAt = (index: number) => {
        const option = optionRefs.current[index]
        if (option) {
            option.focus()
        }
    }

    const moveFocus = (nextIndex: number) => {
        setActiveIndex(nextIndex)
        focusOptionAt(nextIndex)
    }

    const selectDropdownOption = (optionId: ChatMode, returnFocus: boolean) => {
        if (isModeGated(optionId, isPro, gatedModes)) {
            openUpgradeModal()
            closeDropdown(returnFocus)
            return
        }
        if (currentMode === 'ensemble') {
            if (isPromptMode(optionId)) {
                setPromptMode(optionId)
            }
            closeDropdown(returnFocus)
            return
        }
        handleSelect(optionId)
        closeDropdown(returnFocus)
    }

    useEffect(() => {
        const handler = (event: MouseEvent) => {
            if (!dropdownRef.current) return
            if (dropdownRef.current.contains(event.target as Node)) return
            setDropdownOpen(false)
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    useEffect(() => {
        if (!dropdownOpen) return
        if (dropdownOptions.length === 0) return
        const selectedIndex = dropdownOptions.findIndex(option => option.id === currentPromptMode)
        const fallbackIndex = selectedIndex >= 0 ? selectedIndex : 0
        const nextIndex = pendingFocusIndex.current ?? fallbackIndex
        pendingFocusIndex.current = null
        setActiveIndex(nextIndex)
        requestAnimationFrame(() => focusOptionAt(nextIndex))
    }, [dropdownOpen, dropdownOptions, currentPromptMode])

    return (
        <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
                <span className="text-xs uppercase tracking-[0.3em] text-gray-500">Mode</span>
                <span className="text-[11px] text-gray-500">Choose how KnowBear responds</span>
            </div>
            <div className="flex flex-col gap-3 md:flex-row md:items-stretch">
                <div className="relative md:w-[320px]" ref={dropdownRef}>
                    <button
                        ref={triggerRef}
                        type="button"
                        onClick={() => setDropdownOpen(value => !value)}
                        onKeyDown={event => {
                            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                                event.preventDefault()
                                pendingFocusIndex.current = event.key === 'ArrowUp' ? dropdownOptions.length - 1 : 0
                                setDropdownOpen(true)
                            }
                        }}
                        aria-haspopup="listbox"
                        aria-expanded={dropdownOpen}
                        aria-controls={listboxId}
                        aria-label="Select learning mode"
                        className={`w-full rounded-2xl border px-3 py-3 text-left transition ${
                            dropdownActive ? 'border-blue-500/40 bg-dark-900/60' : 'border-white/5 bg-dark-900/40'
                        }`}
                    >
                        <div className="flex items-center justify-between">
                            <div className="flex items-start gap-2">
                                <span className="mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-xl bg-white/5">
                                    <BookOpen className="h-4 w-4 text-cyan-200" />
                                </span>
                                <div className="flex flex-col">
                                    <span className="text-sm font-semibold text-white">
                                        {dropdownActive ? dropdownActive.label : 'Learning Modes'}
                                    </span>
                                    <span className="text-xs text-gray-500">
                                        {dropdownActive ? dropdownActive.description : 'ELI + Classic tones'}
                                    </span>
                                </div>
                            </div>
                            <ChevronDown className={`h-4 w-4 text-gray-500 transition ${dropdownOpen ? 'rotate-180' : ''}`} />
                        </div>
                    </button>

                    {dropdownOpen && (
                        <div
                            id={listboxId}
                            role="listbox"
                            aria-label="Learning modes"
                            className="absolute left-0 right-0 mt-2 z-20 rounded-2xl border border-white/10 bg-dark-900/95 backdrop-blur-xl p-2 shadow-xl"
                        >
                            <div className="grid grid-cols-1 gap-1">
                                {dropdownOptions.map((option, index) => {
                                    const isActive = currentPromptMode === option.id
                                    const isGated = isModeGated(option.id, isPro, gatedModes)
                                    return (
                                        <button
                                            key={option.id}
                                            type="button"
                                            role="option"
                                            aria-selected={isActive}
                                            id={`${listboxId}-${option.id}`}
                                            tabIndex={activeIndex === index ? 0 : -1}
                                            ref={element => {
                                                optionRefs.current[index] = element
                                            }}
                                            onFocus={() => setActiveIndex(index)}
                                            onClick={event => {
                                                const returnFocus = event.detail === 0
                                                selectDropdownOption(option.id, returnFocus)
                                            }}
                                            onKeyDown={event => {
                                                if (event.key === 'ArrowDown') {
                                                    event.preventDefault()
                                                    const nextIndex = (index + 1) % dropdownOptions.length
                                                    moveFocus(nextIndex)
                                                    return
                                                }
                                                if (event.key === 'ArrowUp') {
                                                    event.preventDefault()
                                                    const nextIndex = (index - 1 + dropdownOptions.length) % dropdownOptions.length
                                                    moveFocus(nextIndex)
                                                    return
                                                }
                                                if (event.key === 'Home') {
                                                    event.preventDefault()
                                                    moveFocus(0)
                                                    return
                                                }
                                                if (event.key === 'End') {
                                                    event.preventDefault()
                                                    moveFocus(dropdownOptions.length - 1)
                                                    return
                                                }
                                                if (event.key === 'Escape') {
                                                    event.preventDefault()
                                                    closeDropdown(true)
                                                    return
                                                }
                                                if (event.key === 'Tab') {
                                                    closeDropdown(false)
                                                    return
                                                }
                                                if (event.key === 'Enter' || event.key === ' ') {
                                                    event.preventDefault()
                                                    selectDropdownOption(option.id, true)
                                                }
                                            }}
                                            title={isGated ? 'Pro feature' : undefined}
                                            className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm transition ${
                                                isActive
                                                    ? 'bg-blue-500/10 text-white'
                                                    : 'text-gray-300 hover:bg-white/5'
                                            } ${isGated ? 'opacity-60 cursor-not-allowed' : ''}`}
                                        >
                                            <div className="flex items-center gap-2">
                                                <span className="font-semibold">{option.label}</span>
                                                {isGated && <Lock className="h-3 w-3 text-yellow-400" />}
                                            </div>
                                            <span className="text-xs text-gray-500">{option.description}</span>
                                        </button>
                                    )
                                })}
                            </div>
                        </div>
                    )}
                </div>

                <div className="grid flex-1 grid-cols-2 gap-2 md:grid-cols-4">
                    {quickOptions.map(mode => {
                        const isActive = currentMode === mode.id
                        const isGated = isModeGated(mode.id, isPro, gatedModes)
                        const Icon = MODE_ICONS[mode.id]
                        const accent = CHAT_MODE_ACCENTS[mode.id]
                        return (
                            <button
                                key={mode.id}
                                type="button"
                                onClick={() => handleSelect(mode.id)}
                                title={isGated ? 'Pro feature' : undefined}
                                aria-disabled={isGated}
                                className={`relative overflow-hidden rounded-2xl border px-3 py-3 text-left transition ${
                                    isActive
                                        ? 'border-blue-500/40'
                                        : 'border-white/5 hover:border-white/10'
                                } ${isGated ? 'bg-white/5 text-gray-400 cursor-not-allowed' : 'bg-dark-900/60 text-gray-200'}`}
                            >
                                {isActive && (
                                    <motion.span
                                        layoutId="chat-mode-highlight"
                                        className="absolute inset-0 bg-blue-600"
                                        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
                                    />
                                )}
                                <span className="relative z-10 flex items-start gap-2">
                                    <span className={`mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-xl ${isActive ? 'bg-white/15' : 'bg-white/5'}`}>
                                        <Icon className={`h-4 w-4 ${isActive ? 'text-white' : accent}`} />
                                    </span>
                                    <span className="flex flex-col">
                                        <span className="flex items-center gap-2 text-sm font-semibold">
                                            {mode.label}
                                            {isGated && <Lock className="h-3 w-3 text-yellow-400" />}
                                        </span>
                                        <span className={`text-xs ${isActive ? 'text-blue-100' : 'text-gray-500'}`}>
                                            {mode.description}
                                        </span>
                                    </span>
                                </span>
                            </button>
                        )
                    })}
                </div>
            </div>
        </div>
    )
}
