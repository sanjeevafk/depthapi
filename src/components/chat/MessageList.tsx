import { useEffect, memo, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import Mermaid from "../Mermaid";
import SafeImage from "../SafeImage";
import MessageActionToolbar from "./MessageActionToolbar";
import ShareModal from "../share/ShareModal";
import { useChatStore } from "../../stores/useChatStore";
import { useConversationStore } from "../../stores/useConversationStore";
import { useMessageStore } from "../../stores/useMessageStore";
import { formatModeLabel } from "../../lib/chatModes";
import type { ConversationMode, PromptMode } from "../../types/chat";
import { getStreamingVerbs } from "../streamingVerbs";
import { notifyToast } from "../../lib/toast";

const markdownComponents: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const codeStr = String(children).replace(/\n$/, "");

    if (match && match[1] === "mermaid") {
      return <Mermaid chart={codeStr} />;
    }

    return (
      <code
        className={`${className} bg-black/40 rounded px-1.5 py-0.5 text-xs font-mono`}
        {...props}
      >
        {children}
      </code>
    );
  },
  pre({ children }) {
    return (
      <pre className="bg-black/40 p-4 rounded-xl border border-white/10 overflow-x-auto my-3">
        {children}
      </pre>
    );
  },
  img({ src, alt }) {
    if (!src) return null;
    return <SafeImage src={src} alt={alt || "Image"} />;
  },
  a({ ...props }) {
    return (
      <a
        {...props}
        target="_blank"
        rel="noopener noreferrer"
        className="underline decoration-cyan-500/40 underline-offset-4 hover:decoration-cyan-300"
      />
    );
  },
};

export default function MessageList(): JSX.Element {
  const messageIds = useMessageStore((state) => state.messageIds);
  const isLoading = useConversationStore((state) => state.isLoading);
  const messagesById = useMessageStore((state) => state.messagesById);
  const [shareMessageId, setShareMessageId] = useState<string | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);

  const lastMessageId = messageIds[messageIds.length - 1];
  const lastContent = lastMessageId ? messagesById[lastMessageId]?.content : undefined;
  const handleScroll = () => {
    const container = scrollRef.current;
    if (!container) return;
    const distanceFromBottom =
      container.scrollHeight - (container.scrollTop + container.clientHeight);
    shouldAutoScrollRef.current = distanceFromBottom < 120;
  };

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || !shouldAutoScrollRef.current) return;
    container.scrollTo({
      top: container.scrollHeight,
      behavior: "smooth",
    });
  }, [messageIds.length, lastContent]);

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="flex-1 min-h-0 overflow-y-auto px-3 py-4 sm:px-6 sm:py-6"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col space-y-4">
        {messageIds.length === 0 ? (
          <div className="text-sm text-gray-500">
            Start a conversation to see messages here.
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {messageIds.map((messageId) => (
              <MessageItem
                key={messageId}
                messageId={messageId}
                onShare={(targetId) => {
                  setShareMessageId(targetId);
                  setShareOpen(true);
                }}
              />
            ))}
          </AnimatePresence>
        )}

        {isLoading && messageIds.length === 0 && (
          <div className="text-xs text-gray-500">Loading messages...</div>
        )}
      </div>
      <ShareModal
        open={shareOpen}
        messageId={shareMessageId}
        defaultKind="response"
        allowKindSelection={false}
        onClose={() => {
          setShareOpen(false);
          setShareMessageId(null);
        }}
      />
    </div>
  );
}

function MessageItem({
  messageId,
  onShare,
}: {
  messageId: string;
  onShare: (targetId: string) => void;
}): JSX.Element | null {
  const message = useMessageStore((state) => state.messagesById[messageId]);
  const regenerateMessage = useChatStore((state) => state.regenerateMessage);
  const retrySync = useChatStore((state) => state.retrySync);

  if (!message) return null;

  const isUser = message.role === "user";
  const assistantMode = !isUser ? message.metadata?.mode : undefined;
  const assistantPromptMode = !isUser ? message.metadata?.prompt_mode : undefined;
  let assistantLabel = assistantMode ? formatModeLabel(assistantMode) : undefined;
  if (assistantLabel && assistantMode === "learn" && assistantPromptMode) {
    assistantLabel = `${assistantLabel}-${assistantPromptMode.toUpperCase()}`;
  }
  const shareTargetId = resolveShareMessageId(message);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ duration: 0.2 }}
      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div
        className={`max-w-[90%] sm:max-w-[75%] break-words rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-lg border relative ${!isUser ? "group" : ""} ${
          isUser
            ? "bg-accent-primary text-white border-accent-primary/30"
            : "bg-dark-700 text-gray-100 border-white/5"
        }`}
      >
        {!isUser && (
          <MessageActionToolbar
            content={message.content}
            disabled={message.isStreaming || message.isRegenerating}
            onRegenerate={() => void regenerateMessage(messageId)}
            onShare={() => {
              if (shareTargetId) {
                onShare(shareTargetId);
              } else {
                notifyToast("Share link available once the message is saved.", "info");
              }
            }}
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
          <MessageContent
            content={message.content}
            isStreaming={message.isStreaming}
          />
        </div>
        {message.isRegenerating ? (
          <div
            className="mt-2 flex items-center gap-2 text-xs text-cyan-200"
            aria-live="polite"
          >
            <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
            Regenerating...
          </div>
        ) : (
          message.isStreaming && (
            <StreamingIndicator
              mode={assistantMode}
              promptMode={assistantPromptMode}
            />
          )
        )}
        {message.error && (
          <div className="mt-2 text-xs text-red-400">{message.error}</div>
        )}
        {message.syncStatus === "failed" && message.retryPayload && (
          <button
            onClick={() => void retrySync(messageId)}
            className="mt-2 text-[11px] text-cyan-300 border border-cyan-500/30 rounded-full px-3 py-1 hover:bg-cyan-500/10 transition"
          >
            Retry
          </button>
        )}
      </div>
    </motion.div>
  );
}

function resolveShareMessageId(message: {
  id?: string;
  serverMessageId?: string;
}): string | null {
  const candidate = message.serverMessageId || message.id || "";
  return isUuid(candidate) ? candidate : null;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function StreamingIndicator({
  mode,
  promptMode,
}: {
  mode?: ConversationMode;
  promptMode?: PromptMode;
}): JSX.Element {
  const verbs = useMemo(
    () => getStreamingVerbs(mode, promptMode),
    [mode, promptMode],
  );
  const [index, setIndex] = useState(() =>
    verbs?.length ? Math.floor(Math.random() * verbs.length) : 0,
  );

  useEffect(() => {
    if (!verbs?.length) return;
    setIndex(Math.floor(Math.random() * verbs.length));
  }, [verbs]);

  useEffect(() => {
    if (!verbs?.length) return;
    const intervalId = window.setInterval(() => {
      setIndex((current) => (current + 1) % verbs.length);
    }, 1400);
    return () => window.clearInterval(intervalId);
  }, [verbs]);

  const label = verbs?.length ? verbs[index % verbs.length] : "Streaming";

  return (
    <div className="mt-2 flex items-center gap-2 text-xs text-cyan-200">
      <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
      {label}...
    </div>
  );
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
      <div data-streaming={isStreaming ? "true" : "false"}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={markdownComponents}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  },
  (prev, next) =>
    prev.content === next.content && prev.isStreaming === next.isStreaming,
);
