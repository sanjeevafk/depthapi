import { useState, useRef, useEffect } from 'react'
import type { Level } from '../types'
import { FREE_LEVELS, PREMIUM_LEVELS } from '../types'
import { Lock } from 'lucide-react'
import { useUsageGate } from '../hooks/useUsageGate'

interface LevelDropdownProps {
    selected: Level
    onChange: (level: Level) => void
}

const LEVEL_LABELS: Record<Level, string> = {
    eli5: 'Like I\'m 5',
    eli10: 'Like I\'m 10',
    eli12: 'Like I\'m 12',
    eli15: 'Like I\'m 15',
    meme: 'Meme Style',
    classic60: 'Classic (80s/90s)',
    gentle70: 'Gentle & Kind',
    warm80: 'Warm & Fuzzy',
}

export default function LevelDropdown({ selected, onChange }: LevelDropdownProps) {
    const [isOpen, setIsOpen] = useState(false)
    const dropdownRef = useRef<HTMLDivElement>(null)
    const { isPro, checkAction } = useUsageGate()

    // Close on click outside
    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsOpen(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const handleSelect = (level: Level) => {
        const isPremium = (PREMIUM_LEVELS as readonly Level[]).includes(level)
        if (isPremium) {
            const { allowed } = checkAction('premium_mode')
            if (!allowed) return // Modal handled by hook
        }
        onChange(level)
        setIsOpen(false)
    }



    return (
        <div className="relative w-full md:w-64" ref={dropdownRef}>
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="w-full flex items-center justify-between px-4 py-3 bg-dark-700 hover:bg-dark-600 border border-dark-600 rounded-lg text-white transition-all outline-none focus:border-accent-primary"
            >
                <div className="flex items-center gap-2">
                    <span className="font-medium">{LEVEL_LABELS[selected]}</span>
                    {!isPro && (PREMIUM_LEVELS as readonly Level[]).includes(selected) && (
                        <Lock className="w-3 h-3 text-yellow-500" />
                    )}
                </div>
                <svg
                    className={`w-5 h-5 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {isOpen && (
                <div className="absolute z-50 w-full mt-2 bg-dark-800 border border-dark-600 rounded-lg shadow-xl max-h-60 overflow-y-auto">
                    {/* Free Levels */}
                    <div className="px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">Free</div>
                    {FREE_LEVELS.map((level) => (
                        <button
                            key={level}
                            onClick={() => handleSelect(level)}
                            className={`w-full text-left px-4 py-2 text-sm transition-colors ${selected === level
                                ? 'bg-accent-primary/10 text-accent-primary font-medium'
                                : 'text-gray-300 hover:bg-dark-700'
                                }`}
                        >
                            {LEVEL_LABELS[level]}
                        </button>
                    ))}

                    {/* Premium Levels */}
                    <div className="px-3 py-2 mt-2 text-xs font-semibold text-yellow-500/80 uppercase tracking-wider flex items-center justify-between">
                        <span>Pro</span>
                        {!isPro && <Lock className="w-3 h-3" />}
                    </div>
                    {PREMIUM_LEVELS.map((level) => (
                        <button
                            key={level}
                            onClick={() => handleSelect(level)}
                            className={`w-full text-left px-4 py-2 text-sm transition-colors flex items-center justify-between group ${selected === level
                                ? 'bg-yellow-500/10 text-yellow-500 font-medium'
                                : 'text-gray-300 hover:bg-dark-700'
                                }`}
                        >
                            <span>{LEVEL_LABELS[level]}</span>
                            {!isPro && <Lock className="w-3 h-3 text-gray-600 group-hover:text-yellow-500 transition-colors" />}
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}
