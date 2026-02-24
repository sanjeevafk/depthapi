import { useState, useRef, useEffect } from 'react'
import { Zap, Sparkles, ChevronDown, Lock } from 'lucide-react'
import type { Mode } from '../types'
import { useUsageGate } from '../hooks/useUsageGate'

interface ModeDropdownProps {
    selected: Mode
    onChange: (mode: Mode) => void
    disabled?: boolean
}

const MODES: { id: Mode; label: string; description: string; icon: any; color: string; premium: boolean }[] = [
    {
        id: 'fast',
        label: 'Fast',
        description: 'Speed-optimized, standard answers.',
        icon: Zap,
        color: 'text-cyan-400',
        premium: false
    },
    {
        id: 'ensemble',
        label: 'Ensemble',
        description: 'High-accuracy synthesis from multiple models.',
        icon: Sparkles,
        color: 'text-purple-400',
        premium: true
    }
]

export default function ModeDropdown({ selected, onChange, disabled }: ModeDropdownProps) {
    const [isOpen, setIsOpen] = useState(false)
    const dropdownRef = useRef<HTMLDivElement>(null)
    const { isPro, checkAction } = useUsageGate()

    const selectedMode = MODES.find(m => m.id === selected) || MODES[0]

    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsOpen(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const handleSelect = (modeId: Mode) => {
        if (modeId === selected) {
            setIsOpen(false)
            return
        }

        const mode = MODES.find(m => m.id === modeId)
        if (mode?.premium) {
            const { allowed } = checkAction('premium_mode', modeId)
            if (!allowed) return // Modal handled by hook
        }

        onChange(modeId)
        setIsOpen(false)
    }

    return (
        <div className="relative" ref={dropdownRef}>
            <button
                type="button"
                onClick={() => !disabled && setIsOpen(!isOpen)}
                disabled={disabled}
                className={`
                    flex items-center gap-3 px-4 py-2.5 rounded-xl transition-all duration-300
                    bg-dark-800/80 backdrop-blur-md border border-dark-600 
                    hover:border-dark-400 hover:bg-dark-700
                    active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed
                    focus:outline-none focus:ring-2 focus:ring-cyan-500/50
                    group relative overflow-hidden
                `}
            >
                {/* Shiny effect on hover */}
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent -translate-x-full group-hover:animate-shimmer" />

                <selectedMode.icon className={`w-4 h-4 ${selectedMode.color}`} />
                <div className="flex flex-col items-start">
                    <span className="text-sm font-bold text-white leading-none flex items-center gap-1.5">
                        {selectedMode.label}
                        {selectedMode.premium && !isPro && <Lock className="w-2.5 h-2.5 text-yellow-500/80" />}
                    </span>
                </div>
                <ChevronDown className={`w-4 h-4 text-gray-500 transition-transform duration-300 ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            {isOpen && (
                <div className="absolute bottom-full md:bottom-auto md:top-full left-0 md:right-0 mb-2 md:mb-0 md:mt-2 w-[280px] z-50 animate-in fade-in slide-in-from-top-2 duration-200">
                    <div className="bg-dark-800/95 backdrop-blur-xl border border-dark-600 rounded-2xl shadow-2xl overflow-hidden p-1.5 space-y-1">
                        {MODES.map((m) => (
                            <button
                                key={m.id}
                                onClick={() => handleSelect(m.id)}
                                className={`
                                    w-full flex items-start gap-3 p-3 rounded-xl transition-all group
                                    ${selected === m.id
                                        ? 'bg-cyan-500/10 border border-cyan-500/20'
                                        : 'hover:bg-white/5 border border-transparent'}
                                `}
                            >
                                <div className={`p-2 rounded-lg ${selected === m.id ? 'bg-cyan-500/20' : 'bg-dark-700'} group-hover:scale-110 transition-transform`}>
                                    <m.icon className={`w-4 h-4 ${m.color}`} />
                                </div>
                                <div className="flex flex-col items-start text-left">
                                    <div className="flex items-center gap-2">
                                        <span className={`text-sm font-bold ${selected === m.id ? 'text-cyan-400' : 'text-white'}`}>
                                            {m.label}
                                        </span>
                                        {m.premium && !isPro && (
                                            <span className="bg-yellow-500/10 text-yellow-500 text-[10px] px-1.5 py-0.5 rounded-md font-bold uppercase tracking-wider flex items-center gap-1">
                                                <Lock className="w-2.5 h-2.5" /> PRO
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-xs text-gray-400 mt-0.5 leading-snug">
                                        {m.description}
                                    </p>
                                </div>
                            </button>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}
