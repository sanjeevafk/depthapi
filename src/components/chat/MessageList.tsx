import { useEffect, useMemo, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Mermaid from '../Mermaid'
import SafeImage from '../SafeImage'
import { useChatStore } from '../../stores/useChatStore'

export default function MessageList() {
    const messages = useChatStore(state => state.messages)
    const isLoading = useChatStore(state => state.isLoading)
    const scrollRef = useRef<HTMLDivElement>(null)

    const lastContent = useMemo(() => messages[messages.length - 1]?.content, [messages])

    useEffect(() => {
        if (!scrollRef.current) return
        scrollRef.current.scrollTo({
            top: scrollRef.current.scrollHeight,
            behavior: 'smooth',
        })
    }, [messages.length, lastContent])

    return (
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
            {messages.length === 0 ? (
                <div className="text-sm text-gray-500">Start a conversation to see messages here.</div>
            ) : (
                <AnimatePresence initial={false}>
                    {messages.map(message => {
                        const isUser = message.role === 'user'
                        const key = message.clientGeneratedId || message.id
                        return (
                            <motion.div
                                key={key}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                transition={{ duration: 0.2 }}
                                className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
                            >
                                <div
                                    className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-lg border ${
                                        isUser
                                            ? 'bg-accent-primary text-white border-accent-primary/30'
                                            : 'bg-dark-700 text-gray-100 border-white/5'
                                    }`}
                                >
                                    <ReactMarkdown
                                        remarkPlugins={[remarkGfm]}
                                        className="text-sm leading-relaxed"
                                        components={{
                                            code({ inline, className, children, ...props }: any) {
                                                const match = /language-(\w+)/.exec(className || '')
                                                const codeStr = String(children).replace(/\n$/, '')

                                                if (!inline && match && match[1] === 'mermaid') {
                                                    return <Mermaid chart={codeStr} />
                                                }

                                                return (
                                                    <code
                                                        className={`${className} bg-black/40 rounded px-1.5 py-0.5 text-xs font-mono`}
                                                        {...props}
                                                    >
                                                        {children}
                                                    </code>
                                                )
                                            },
                                            pre({ children }) {
                                                return (
                                                    <pre className="bg-black/40 p-4 rounded-xl border border-white/10 overflow-x-auto my-3">
                                                        {children}
                                                    </pre>
                                                )
                                            },
                                            img({ src, alt }: any) {
                                                return <SafeImage src={src} alt={alt || 'Image'} />
                                            },
                                            a({ node, ...props }: any) {
                                                return (
                                                    <a
                                                        {...props}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="underline decoration-cyan-500/40 underline-offset-4 hover:decoration-cyan-300"
                                                    />
                                                )
                                            },
                                        }}
                                    >
                                        {message.content}
                                    </ReactMarkdown>
                                    {message.isStreaming && (
                                        <div className="mt-2 flex items-center gap-2 text-xs text-cyan-200">
                                            <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
                                            Streaming...
                                        </div>
                                    )}
                                    {message.error && (
                                        <div className="mt-2 text-xs text-red-400">
                                            {message.error}
                                        </div>
                                    )}
                                </div>
                            </motion.div>
                        )
                    })}
                </AnimatePresence>
            )}

            {isLoading && messages.length === 0 && (
                <div className="text-xs text-gray-500">Loading messages...</div>
            )}
        </div>
    )
}
