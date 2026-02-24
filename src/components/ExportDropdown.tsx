import { useState, useRef, useEffect } from 'react'
import { exportExplanations } from '../api'
import { useUsageGate } from '../hooks/useUsageGate'
import { Lock, Download, ChevronDown, Copy, Check } from 'lucide-react'
import type { Mode } from '../types'

interface ExportDropdownProps {
    topic: string
    explanations: Record<string, string>
    compact?: boolean
    mode: Mode
}

const EXPORT_LABELS: Record<string, string> = {
    md: 'Markdown (.md)',
    txt: 'Text File (.txt)',
    copy: 'Copy Markdown'
}

export default function ExportDropdown({ topic, explanations, compact = false, mode }: ExportDropdownProps) {
    const [isOpen, setIsOpen] = useState(false)
    const [loading, setLoading] = useState(false)
    const [copied, setCopied] = useState(false)
    const dropdownRef = useRef<HTMLDivElement>(null)
    const { checkAction, isPro } = useUsageGate()

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

    const handleExport = async (format: 'txt' | 'md' | 'copy') => {
        setIsOpen(false)

        const { allowed } = checkAction('export_data')
        if (!allowed) return

        if (format === 'copy') {
            const markdown = generateMarkdown()
            await navigator.clipboard.writeText(markdown)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
            return
        }

        setLoading(true)
        const slug = topic.toLowerCase().replace(/\s+/g, '_').slice(0, 30)
        const filename = `knowbear-${slug}.${format}`

        try {
            if (format === 'md' || format === 'txt') {
                const req = {
                    topic,
                    explanations,
                    format,
                    premium: isPro,
                    mode: mode
                }
                const blob = await exportExplanations(req as any)
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = filename
                a.click()
                URL.revokeObjectURL(url)
            }
        } catch (err) {
            console.error('Export failed:', err)
        } finally {
            setLoading(false)
        }
    }

    const generateMarkdown = () => {
        // Start with the main topic header
        let markdown = `# ${topic}\n\n`

        // Add a horizontal rule after title if multiple levels exist
        if (Object.keys(explanations).length > 1) {
            markdown += `---\n\n`
        }

        for (const [level, text] of Object.entries(explanations)) {
            // Only add ELI headers if we are in multi-level mode
            if (Object.keys(explanations).length > 1) {
                const label = level.replace('eli', 'ELI-').toUpperCase()
                markdown += `## ${label}\n\n`
            }

            markdown += `${text.trim()}\n\n`

            // Separator between levels
            if (Object.keys(explanations).length > 1) {
                markdown += `---\n\n`
            }
        }

        return markdown.trim()
    }

    return (
        <div className={`relative ${compact ? '' : 'w-full md:w-48'}`} ref={dropdownRef}>
            <button
                onClick={() => setIsOpen(!isOpen)}
                disabled={loading}
                className={`flex items-center justify-between bg-dark-700 hover:bg-dark-600 border border-dark-600 rounded-xl text-white transition-all outline-none focus:border-cyan-500 disabled:opacity-50 ${compact ? 'p-3' : 'px-4 py-3'}`}
            >
                {compact ? (
                    copied ? <Check className="w-5 h-5 text-green-400" /> : <Download className={`w-5 h-5 ${loading ? 'animate-pulse' : ''}`} />
                ) : (
                    <>
                        <div className="flex items-center gap-2">
                            <span className="font-medium">
                                {loading ? 'Exporting...' : copied ? 'Copied!' : 'Export'}
                            </span>
                            {!isPro && <Lock className="w-3 h-3 text-yellow-500" />}
                        </div>
                        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                    </>
                )}
            </button>

            {isOpen && (
                <div className={`absolute z-50 w-56 md:w-full bottom-full md:bottom-auto md:top-full mb-2 md:mb-0 md:mt-2 right-0 bg-dark-800 border border-dark-600 rounded-xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-bottom-2 md:slide-in-from-top-2 duration-200`}>
                    <div className="px-3 py-2 text-[10px] font-bold text-gray-500 uppercase tracking-widest border-b border-dark-700/50">
                        Select Action
                    </div>
                    <button
                        onClick={() => handleExport('copy')}
                        className="w-full text-left px-4 py-3 text-sm text-gray-300 hover:bg-white/5 hover:text-white transition-colors flex items-center justify-between group"
                    >
                        <div className="flex items-center gap-2">
                            <Copy size={14} className="text-gray-500 group-hover:text-cyan-400 transition-colors" />
                            <span>Copy Markdown</span>
                        </div>
                    </button>
                    {(['md', 'txt'] as const).map((fmt) => (
                        <button
                            key={fmt}
                            onClick={() => handleExport(fmt)}
                            className="w-full text-left px-4 py-3 text-sm text-gray-300 hover:bg-white/5 hover:text-white transition-colors flex items-center justify-between group"
                        >
                            <span>{EXPORT_LABELS[fmt]}</span>
                            {!isPro && (
                                <Lock size={14} className="text-yellow-500 group-hover:scale-110 transition-transform" />
                            )}
                        </button>
                    ))}
                    {/* 2026-01: PDF and JSON export disabled for Vercel bundle limits */}
                    {/* 
                    <div className="px-4 py-2 text-[9px] text-gray-600 italic border-t border-dark-700/30">
                        PDF export temporarily unavailable
                    </div>
                    */}
                </div>
            )}
        </div >
    )
}
