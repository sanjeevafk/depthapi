import { useState, useRef, useEffect } from 'react'
import { Search, Loader2, Sparkles } from 'lucide-react'
import type { Mode } from '../types'
import ModeDropdown from './ModeDropdown'

interface SearchBarProps {
    onSearch: (topic: string) => void
    loading: boolean
    mode: Mode
    onModeChange: (mode: Mode) => void
    value?: string
}

export default function SearchBar({ onSearch, loading, mode, onModeChange, value = '' }: SearchBarProps) {
    const [topic, setTopic] = useState(value)
    const [isFocused, setIsFocused] = useState(false)
    const [placeholder, setPlaceholder] = useState("What passes for knowledge...?")

    useEffect(() => {
        setTopic(value)
    }, [value])

    useEffect(() => {
        const updatePlaceholder = () => {
            if (window.innerWidth <= 640) {
                setPlaceholder("Search topics...")
            } else {
                setPlaceholder("What passes for knowledge...?")
            }
        }

        updatePlaceholder()
        window.addEventListener('resize', updatePlaceholder)
        return () => window.removeEventListener('resize', updatePlaceholder)
    }, [])
    const inputRef = useRef<HTMLInputElement>(null)

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === '/' && !isFocused) {
                e.preventDefault()
                inputRef.current?.focus()
            }
        }
        window.addEventListener('keydown', handleKeyDown)
        return () => window.removeEventListener('keydown', handleKeyDown)
    }, [isFocused])

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault()
        if (topic.trim() && !loading) {
            onSearch(topic)
        }
    }

    return (
        <div className="w-full max-w-3xl mx-auto space-y-4">
            <form onSubmit={handleSubmit} className="relative group z-20">
                <div className={`absolute -inset-0.5 bg-gradient-to-r from-cyan-500 to-purple-500 rounded-2xl opacity-20 group-hover:opacity-40 transition duration-500 ${isFocused ? 'opacity-60' : ''}`}></div>
                <div className="relative flex items-center bg-dark-800 border border-dark-600 rounded-2xl p-2 transition-all shadow-xl group-hover:border-dark-500">
                    <Search className={`ml-3 w-5 h-5 transition-colors ${isFocused ? 'text-cyan-400' : 'text-gray-500'}`} />
                    <input
                        ref={inputRef}
                        type="text"
                        value={topic}
                        onChange={(e) => setTopic(e.target.value)}
                        onFocus={() => setIsFocused(true)}
                        onBlur={() => setIsFocused(false)}
                        placeholder={placeholder}
                        className="w-full bg-transparent text-white placeholder-gray-500 px-4 py-3 outline-none text-base md:text-lg"
                        disabled={loading}
                    />

                    <div className="hidden md:flex items-center gap-3 mr-2">
                        <ModeDropdown selected={mode} onChange={onModeChange} disabled={loading} />
                        <div className="flex items-center gap-2 text-[10px] text-dark-400 font-bold uppercase tracking-widest border-l border-dark-700 pl-3">
                            <kbd className="bg-dark-700/50 px-1.5 py-0.5 rounded border border-dark-600">/</kbd>
                            <span>Focus</span>
                        </div>
                    </div>

                    <button
                        type="submit"
                        disabled={!topic.trim() || loading}
                        className="ml-2 px-6 py-2.5 bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white rounded-xl font-bold transition-all transform active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 shadow-lg shadow-cyan-900/20"
                    >
                        {loading ? (
                            <Loader2 className="w-5 h-5 animate-spin" />
                        ) : (
                            <>
                                <Sparkles className="w-4 h-4" />
                                <span>Explain</span>
                            </>
                        )}
                    </button>
                </div>
            </form>

            {/* Mobile Mode Selection (visible only on small screens, outside search bar for better space) */}
            <div className="flex md:hidden justify-center py-2">
                <ModeDropdown selected={mode} onChange={onModeChange} disabled={loading} />
            </div>
        </div>
    )
}
