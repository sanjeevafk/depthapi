import type { Level } from '../types'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Mermaid from './Mermaid'
import SafeImage from './SafeImage'

interface ExplanationCardProps {
    level: Level
    content: string
    streaming?: boolean
}

const LEVEL_COLORS: Record<Level, string> = {
    eli5: 'border-l-green-500',
    eli10: 'border-l-teal-500',
    eli12: 'border-l-cyan-500',
    eli15: 'border-l-blue-500',
    meme: 'border-l-purple-500',
    classic60: 'border-l-yellow-500',
    gentle70: 'border-l-indigo-500',
    warm80: 'border-l-rose-500',
}

const LEVEL_NAMES: Record<Level, string> = {
    eli5: 'Explain Like I\'m 5',
    eli10: 'Explain Like I\'m 10',
    eli12: 'Explain Like I\'m 12',
    eli15: 'Explain Like I\'m 15',
    meme: 'Meme Explanation',
    classic60: 'Classic Mode',
    gentle70: 'Gentle Mode',
    warm80: 'Warm Mode',
}

export default function ExplanationCard({ level, content, streaming }: ExplanationCardProps) {
    const borderColor = LEVEL_COLORS[level]

    return (
        <div
            className={`bg-dark-700 border-l-4 ${borderColor} rounded-lg p-6 transition-all shadow-2xl relative`}
        >
            <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4 border-b border-white/5 pb-2 flex items-center gap-2">
                {LEVEL_NAMES[level]}
            </h3>

            <div className="prose prose-invert max-w-none text-gray-200 leading-relaxed prose-headings:text-white prose-a:text-cyan-400 hover:prose-a:text-cyan-300 prose-code:text-cyan-300 prose-img:rounded-xl prose-hr:border-white/5">
                <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                        code({ node, inline, className, children, ...props }: any) {
                            const match = /language-(\w+)/.exec(className || '')
                            const codeStr = String(children).replace(/\n$/, '')

                            if (!inline && match && match[1] === 'mermaid') {
                                return <Mermaid chart={codeStr} />
                            }

                            return (
                                <code className={`${className} bg-dark-900 rounded px-1.5 py-0.5 text-xs font-mono`} {...props}>
                                    {children}
                                </code>
                            )
                        },
                        pre({ children }) {
                            return <pre className="bg-dark-900 p-4 rounded-xl border border-white/5 overflow-x-auto my-4">{children}</pre>
                        },
                        img({ src, alt }: any) {
                            return <SafeImage src={src} alt={alt || 'Image'} />
                        },
                        a({ node, ...props }: any) {
                            return <a {...props} target="_blank" rel="noopener noreferrer" className="underline decoration-cyan-500/30 underline-offset-4 hover:decoration-cyan-400 transition-all font-medium" />
                        },
                        h2({ children }) {
                            return <h2 className="text-2xl font-bold mt-8 mb-4 text-white border-b border-white/5 pb-2">{children}</h2>
                        },
                        h3({ children }) {
                            return <h3 className="text-xl font-semibold mt-6 mb-3 text-cyan-100">{children}</h3>
                        },
                        hr() {
                            return <hr className="my-8 border-white/10" />
                        }
                    }}
                >
                    {content}
                </ReactMarkdown>
                {streaming && (
                    <span className="inline-block w-2 h-5 ml-1 bg-cyan-500 animate-[pulse_0.8s_infinite] vertical-middle" style={{ verticalAlign: 'middle' }}></span>
                )}
            </div>
        </div>
    )
}

