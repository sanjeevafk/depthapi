import { RefreshCcw, Menu, ChevronLeft } from 'lucide-react'
import ExportDropdown from './ExportDropdown'
import type { Mode } from '../types'

interface MobileBottomNavProps {
    onRegenerate: () => void
    topic: string
    explanations: Record<string, string>
    loading: boolean
    hasResult: boolean
    isSidebarOpen: boolean
    onToggleSidebar: () => void
    mode: Mode
}

export default function MobileBottomNav({
    onRegenerate,
    topic,
    explanations,
    loading,
    hasResult,
    isSidebarOpen,
    onToggleSidebar,
    mode
}: MobileBottomNavProps) {
    if (!hasResult && !loading) return null

    return (
        <>
            {/* Spacer to prevent content overlap on mobile */}
            <div className="h-24 md:hidden" />
            
            <div className="fixed bottom-0 left-0 right-0 z-50 md:hidden">
                <div className="mx-4 mb-4 pb-safe">
                    <div className="bg-dark-900/95 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl p-2 flex items-center justify-between gap-2 relative">
                        {/* Animated background pulse */}
                        <div className="absolute inset-0 bg-gradient-to-r from-cyan-500/5 via-purple-500/5 to-cyan-500/5 animate-pulse rounded-2xl -z-10" />

                    <div className="flex items-center gap-2 relative z-10 w-full justify-between">
                        <button
                            onClick={onToggleSidebar}
                            className="p-3 bg-dark-700 hover:bg-dark-600 border border-dark-600 rounded-xl text-white transition-all active:scale-95"
                            title="Menu"
                        >
                            {isSidebarOpen ? <ChevronLeft className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
                        </button>

                        <div className="flex items-center gap-2">
                            <button
                                onClick={onRegenerate}
                                disabled={loading}
                                className="p-3 bg-dark-700 hover:bg-dark-600 border border-dark-600 rounded-xl text-white transition-all active:scale-95 disabled:opacity-50"
                                title="Regenerate"
                            >
                                <RefreshCcw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
                            </button>

                            <div className="h-8 w-px bg-white/10 mx-1" />

                            <ExportDropdown topic={topic} explanations={explanations} compact mode={mode} />
                        </div>
                    </div>
                </div>
                </div>
            </div>
        </>
    )
}
