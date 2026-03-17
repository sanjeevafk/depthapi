import { memo, useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Mermaid from '../Mermaid'
import SafeImage from '../SafeImage'
import MessageActionToolbar from './MessageActionToolbar'
import { useChatStore } from '../../stores/useChatStore'
import { formatModeLabel } from '../../lib/chatModes'

const markdownComponents: Components = {
    code({ inline, className, children, ...props }) {
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
    img({ src, alt }) {
        if (!src) return null
        return <SafeImage src={src} alt={alt || 'Image'} />
    },
    a({ ...props }) {
        return (
            <a
                {...props}
                target="_blank"
                rel="noopener noreferrer"
                className="underline decoration-cyan-500/40 underline-offset-4 hover:decoration-cyan-300"
            />
        )
    },
}

export default function MessageList(): JSX.Element {
    const messageIds = useChatStore(state => state.messageIds)
    const isLoading = useChatStore(state => state.isLoading)
    const scrollRef = useRef<HTMLDivElement>(null)

    const lastMessageId = messageIds[messageIds.length - 1]
    const lastContent = useChatStore(state => (lastMessageId ? state.messagesById[lastMessageId]?.content : undefined))
    useEffect(() => {
        if (!scrollRef.current) return
        scrollRef.current.scrollTo({
            top: scrollRef.current.scrollHeight,
            behavior: 'smooth',
        })
    }, [messageIds.length, lastContent])

    return (
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
            {messageIds.length === 0 ? (
                <div className="text-sm text-gray-500">Start a conversation to see messages here.</div>
            ) : (
                <AnimatePresence initial={false}>
                    {messageIds.map(messageId => (
                        <MessageItem key={messageId} messageId={messageId} />
                    ))}
                </AnimatePresence>
            )}

            {isLoading && messageIds.length === 0 && (
                <div className="text-xs text-gray-500">Loading messages...</div>
            )}
        </div>
    )
}

function MessageItem({ messageId }: { messageId: string }): JSX.Element | null {
    const message = useChatStore(state => state.messagesById[messageId])
    const openRegenerationModal = useChatStore(state => state.openRegenerationModal)
    const retrySync = useChatStore(state => state.retrySync)

    if (!message) return null

    const isUser = message.role === 'user'
    const assistantMode = !isUser ? message.metadata?.mode : undefined
    const assistantLabel = assistantMode ? formatModeLabel(assistantMode) : undefined

    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
        >
            <div
                className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-lg border relative ${!isUser ? 'group' : ''} ${
                    isUser
                        ? 'bg-accent-primary text-white border-accent-primary/30'
                        : 'bg-dark-700 text-gray-100 border-white/5'
                }`}
            >
                {!isUser && (
                    <MessageActionToolbar
                        content={message.content}
                        disabled={message.isStreaming}
                        onRegenerate={() => openRegenerationModal(messageId)}
                    />
                )}
                {assistantLabel && (
                    <div className="mb-2">
                        <span className="text-[10px] uppercase tracking-[0.2em] px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-gray-300">
                            {assistantLabel}
                        </span>
                    </div>
                )}
                <div className="text-sm leading-relaxed">
                    <MessageContent content={message.content} isStreaming={message.isStreaming} />
                </div>
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
                {message.syncStatus === 'failed' && message.retryPayload && (
                    <button
                        onClick={() => void retrySync(messageId)}
                        className="mt-2 text-[11px] text-cyan-300 border border-cyan-500/30 rounded-full px-3 py-1 hover:bg-cyan-500/10 transition"
                    >
                        Retry Sync
                    </button>
                )}
            </div>
        </motion.div>
    )
}

const MessageContent = memo(
    function MessageContent({
        content,
        isStreaming,
    }: {
        content: string;
        isStreaming?: boolean;
    }): JSX.Element {
        return (
            <div data-streaming={isStreaming ? 'true' : 'false'}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                    {content}
                </ReactMarkdown>
            </div>
        )
    },
    (prev, next) => prev.content === next.content && prev.isStreaming === next.isStreaming
)
