import { useState, useEffect } from 'react'
import { useAuth } from '../context/AuthContext'
import { getHistory, deleteHistoryItem, clearHistory } from '../api'
import { responseCache } from '../lib/responseCache'
import {
    Clock,
    LogOut,
    User as UserIcon,
    ChevronLeft,
    ChevronRight,
    Trash2,
    Crown,
    LogIn,
    MessageSquare,
    AlertTriangle
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { Mode, Level } from '../types'

interface SidebarProps {
    onSelectTopic: (topic: string, mode?: Mode, level?: Level) => void
    refreshTrigger?: number
    isOpen: boolean
    onToggle: () => void
}

export default function Sidebar({ onSelectTopic, refreshTrigger, isOpen, onToggle }: SidebarProps) {
    const [history, setHistory] = useState<any[]>(() => {
        // Instant load from cache to prevent flicker
        const cached = localStorage.getItem('kb_history_cache')
        return cached ? JSON.parse(cached) : []
    })
    const [isLoading, setIsLoading] = useState(false)
    const [showDeleteModal, setShowDeleteModal] = useState(false)
    const [isDeletingAll, setIsDeletingAll] = useState(false)
    const { user, profile, signOut, signInWithGoogle } = useAuth()
    const navigate = useNavigate()

    useEffect(() => {
        if (user) {
            loadHistory()
        } else {
            setHistory([])
            localStorage.removeItem('kb_history_cache')
        }
    }, [user, refreshTrigger])

    const loadHistory = async () => {
        if (history.length === 0) setIsLoading(true)
        try {
            const data = await getHistory()
            setHistory(data)
            // Update cache for next reload
            localStorage.setItem('kb_history_cache', JSON.stringify(data))
        } catch (err) {
            console.error('Failed to load history:', err)
        } finally {
            setIsLoading(false)
        }
    }

    const handleDelete = async (e: React.MouseEvent, id: string) => {
        e.stopPropagation()
        try {
            await deleteHistoryItem(id)
            setHistory(prev => prev.filter(item => item.id !== id))
        } catch (err) {
            console.error('Failed to delete history item:', err)
        }
    }

    const handleDeleteAll = async () => {
        setIsDeletingAll(true)
        // Optimistic update
        const previousHistory = [...history]
        setHistory([])
        localStorage.removeItem('kb_history_cache')
        setShowDeleteModal(false)

        try {
            await clearHistory()
        } catch (err) {
            console.error('Failed to clear history:', err)
            // Rollback on error
            setHistory(previousHistory)
            localStorage.setItem('kb_history_cache', JSON.stringify(previousHistory))
            alert('Failed to clear history. Please try again.')
        } finally {
            setIsDeletingAll(false)
        }
    }

    return (
        <>
            {/* Mobile Backdrop */}
            {isOpen && (
                <div
                    className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 md:hidden animate-in fade-in duration-300"
                    onClick={onToggle}
                />
            )}

            <aside
                className={`fixed left-0 top-0 h-full bg-dark-900 border-r border-dark-700 transition-all duration-300 z-50 flex flex-col 
                    ${isOpen ? 'w-64 translate-x-0' : 'w-64 -translate-x-full md:translate-x-0 md:w-16'}`}
            >
                {/* Toggle Button - Hide on mobile since it's in the bottom nav */}
                <button
                    onClick={onToggle}
                    className="absolute -right-4 top-1/2 -translate-y-1/2 bg-dark-800 border border-dark-600 rounded-full p-1 text-gray-400 hover:text-white transition-colors hidden md:block"
                    aria-label={isOpen ? "Close Sidebar" : "Open Sidebar"}
                >
                    {isOpen ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
                </button>

                {/* Logo area */}
                <div className={`p-4 flex items-center gap-3 border-b border-dark-700 ${!isOpen && 'justify-center'}`}>
                    <div onClick={() => navigate('/app')} className="cursor-pointer">
                        <img src="/favicon.svg" alt="Logo" className="w-8 h-8 drop-shadow-[0_0_8px_rgba(6,182,212,0.4)]" />
                    </div>
                    {isOpen && <span className="text-white font-bold text-lg tracking-tight">Know<span className="text-cyan-500">Bear</span></span>}
                </div>

                {/* User Profile Area */}
                <div className={`p-4 border-b border-dark-700 ${!isOpen && 'flex justify-center'}`}>
                    {user ? (
                        <div className="flex flex-col gap-2">
                            <div className="flex items-center gap-3">
                                <div className="w-8 h-8 rounded-full bg-cyan-600 flex items-center justify-center text-white overflow-hidden ring-2 ring-cyan-500/20">
                                    {user.user_metadata.avatar_url ? (
                                        <img src={user.user_metadata.avatar_url} alt="Avatar" className="w-full h-full object-cover" />
                                    ) : (
                                        <UserIcon size={16} />
                                    )}
                                </div>
                                {isOpen && (
                                    <div className="flex flex-col overflow-hidden">
                                        <span className="text-white text-sm font-medium truncate">
                                            {user.user_metadata.full_name || user.email?.split('@')[0]}
                                        </span>
                                        {profile?.is_pro ? (
                                            <span className="text-cyan-400 text-[10px] items-center gap-1 inline-flex">
                                                <Crown size={10} /> Pro Plan
                                            </span>
                                        ) : (
                                            <span className="text-gray-500 text-[10px] items-center gap-1 inline-flex">
                                                <UserIcon size={10} /> Free Account
                                            </span>
                                        )}
                                    </div>
                                )}
                            </div>
                            {isOpen && (
                                <button
                                    onClick={() => signOut()}
                                    className="mt-2 flex items-center gap-2 text-gray-400 hover:text-white text-xs transition-colors px-1"
                                >
                                    <LogOut size={12} /> Sign Out
                                </button>
                            )}
                        </div>
                    ) : (
                        isOpen ? (
                            <div className="flex flex-col gap-3">
                                <p className="text-gray-400 text-xs px-1 leading-relaxed">Sign in to save search history and sync across devices.</p>
                                <button
                                    onClick={signInWithGoogle}
                                    className="flex items-center justify-center gap-2 py-2 bg-dark-700 hover:bg-dark-600 border border-dark-600 rounded-lg text-white text-sm font-medium transition-all active:scale-95"
                                >
                                    <LogIn size={14} /> Login with Google
                                </button>
                            </div>
                        ) : (
                            <button onClick={signInWithGoogle} className="text-gray-400 hover:text-white transition-colors" title="Login">
                                <LogIn size={20} />
                            </button>
                        )
                    )}
                </div>

                {/* History List */}
                <nav className="flex-grow overflow-y-auto custom-scrollbar p-2">
                    {isOpen ? (
                        <div className="space-y-1">
                            <div className="flex items-center justify-between px-2 py-2 mb-1">
                                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-widest flex items-center gap-2">
                                    <Clock size={12} /> History
                                </h3>
                                <div className="flex items-center gap-2">
                                    {history.length > 0 && (
                                        <>
                                            <button
                                                onClick={() => setShowDeleteModal(true)}
                                                disabled={isDeletingAll}
                                                className="text-[10px] text-gray-500 hover:text-red-400 transition-colors uppercase tracking-tighter font-bold disabled:opacity-50"
                                            >
                                                {isDeletingAll ? 'Clearing...' : 'Clear All'}
                                            </button>
                                            <span className="text-[10px] bg-dark-700 text-gray-400 px-1.5 py-0.5 rounded-full">
                                                {history.length}
                                            </span>
                                        </>
                                    )}
                                </div>
                            </div>
                            {history.length > 0 ? (
                                <div className="space-y-0.5">
                                    {history.map(item => (
                                        <div
                                            key={item.id}
                                            onClick={() => {
                                                const effectiveMode = item.mode === 'ensemble' ? 'ensemble' : 'fast'
                                                // Check if response is cached before triggering search
                                                const cached = responseCache.get(item.topic, effectiveMode)
                                                if (cached) {
                                                    console.log('✅ History click: using cached response for', item.topic, effectiveMode)
                                                } else {
                                                    console.log('⏳ History click: cache miss, will fetch', item.topic, effectiveMode)
                                                }
                                                onSelectTopic(item.topic, effectiveMode, item.levels?.[0] as Level)
                                            }}
                                            className="group flex items-center justify-between p-2.5 rounded-lg hover:bg-dark-800 cursor-pointer transition-all border border-transparent hover:border-dark-700"
                                        >
                                            <div className="flex items-center gap-2.5 min-w-0 flex-1">
                                                <MessageSquare size={14} className="text-gray-500 shrink-0" />
                                                <span className="text-gray-300 text-sm truncate flex-1">{item.topic}</span>
                                                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md uppercase tracking-wider shrink-0 whitespace-nowrap ${item.mode === 'ensemble'
                                                    ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                                                    : 'bg-gray-500/10 text-gray-400 border border-gray-500/20'
                                                    }`}>
                                                    {item.mode === 'ensemble' ? 'Ens' : 'Fast'}
                                                </span>
                                            </div>
                                            <button
                                                onClick={(e) => handleDelete(e, item.id)}
                                                className="opacity-0 group-hover:opacity-100 p-1 text-gray-500 hover:text-red-400 transition-all rounded-md hover:bg-red-400/10 ml-2"
                                                title="Delete"
                                            >
                                                <Trash2 size={13} />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            ) : isLoading ? (
                                <div className="px-2 py-4 space-y-2 animate-pulse">
                                    {[1, 2, 3].map(i => (
                                        <div key={i} className="h-8 bg-dark-700 rounded-md w-full opacity-50"></div>
                                    ))}
                                </div>
                            ) : (
                                <div className="px-2 py-8 text-center">
                                    <p className="text-gray-600 text-sm italic">No recent searches</p>
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="flex flex-col items-center gap-6 py-4">
                            <Clock size={20} className="text-gray-600" />
                        </div>
                    )}
                </nav>

                {/* Footer / App Version + Cache Stats */}
                {isOpen && (
                    <div className="p-4 border-t border-dark-700 space-y-2">
                        <div className="flex items-center justify-between">
                            <span className="text-[10px] text-gray-600 font-mono">v2.0.0-beta</span>
                            <button
                                onClick={() => {
                                    const stats = responseCache.getStats()
                                    console.log('📦 Cache stats before clear:', stats)
                                    responseCache.clear()
                                    console.log('🗑️ Cache cleared')
                                }}
                                className="text-[10px] text-gray-600 hover:text-cyan-400 font-mono transition-colors"
                                title="Clear response cache"
                            >
                                Clear Cache
                            </button>
                        </div>
                    </div>
                )}
            </aside>

            {/* Custom Confirmation Modal */}
            {showDeleteModal && (
                <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/80 backdrop-blur-md" onClick={() => setShowDeleteModal(false)}></div>
                    <div className="relative bg-dark-800 border border-dark-600 rounded-2xl p-6 w-full max-w-sm shadow-2xl animate-in zoom-in-95 duration-200">
                        <div className="flex items-center gap-4 mb-4">
                            <div className="w-12 h-12 bg-red-500/10 rounded-full flex items-center justify-center shrink-0">
                                <AlertTriangle className="text-red-500 w-6 h-6" />
                            </div>
                            <div>
                                <h4 className="text-white font-bold text-lg leading-tight">Clear history?</h4>
                                <p className="text-gray-400 text-sm mt-1">This will permanently delete all your previous searches.</p>
                            </div>
                        </div>
                        <div className="flex gap-3 justify-end mt-6">
                            <button
                                onClick={() => setShowDeleteModal(false)}
                                className="px-4 py-2 text-gray-400 hover:text-white text-sm font-medium transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleDeleteAll}
                                className="px-5 py-2 bg-red-600 hover:bg-red-500 text-white text-sm font-bold rounded-xl transition-all active:scale-95 shadow-lg shadow-red-900/20"
                            >
                                Yes, Clear All
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    )
}
