import { useChatStore } from "../stores/useChatStore";
import { useConversationStore } from "../stores/useConversationStore";
import { useMessageStore } from "../stores/useMessageStore";
import { createConversationInDb } from "../services/dbService";
import { sendChat } from "../services/messageFlowService";
import { trackTelemetry, captureFrontendError } from "../lib/monitoring";
import {
  makeLocalId,
  makeClientId,
  truncateTitle,
  notifyError,
  isAbortError,
  getErrorMessage,
  PENDING_SYNC_KEY,
} from "../lib/chatStoreUtils";
import { isModeGated, isPromptMode } from "../lib/chatModes";
import type { ChatMode, PromptMode, Conversation } from "../types/chat";
import type { ApiError } from "../lib/httpErrors";

interface PendingSyncEntry {
  id: string;
  content: string;
  mode: ChatMode;
  promptMode?: PromptMode;
  createdAt: string;
  clientMessageId?: string;
  assistantClientId?: string;
}

const loadPendingSyncs = (): PendingSyncEntry[] => {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PENDING_SYNC_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is PendingSyncEntry =>
        typeof item === "object" &&
        item !== null &&
        typeof item.id === "string" &&
        typeof item.content === "string" &&
        typeof item.mode === "string",
    );
  } catch {
    return [];
  }
};

const savePendingSyncs = (entries: PendingSyncEntry[]) => {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PENDING_SYNC_KEY, JSON.stringify(entries));
};

export const cachePendingSync = (entry: PendingSyncEntry) => {
  const existing = loadPendingSyncs();
  const next = [entry, ...existing.filter((i) => i.id !== entry.id)].slice(0, 50);
  savePendingSyncs(next);
};

export const removePendingSync = (id: string) => {
  savePendingSyncs(loadPendingSyncs().filter((i) => i.id !== id));
};

interface SendMessageOptions {
  mode?: ChatMode;
  promptMode?: PromptMode;
  isRegeneration?: boolean;
  temperature?: number;
  clientMessageId?: string;
  assistantClientId?: string;
  skipUserMessage?: boolean;
  replaceMessageId?: string;
}

export function useMessageSender() {
  const sendMessage = async (content: string, options?: SendMessageOptions) => {
    const trimmed = content.trim();
    if (!trimmed) return;

    const chatStore = useChatStore.getState();
    const convStore = useConversationStore.getState();
    const msgStore = useMessageStore.getState();

    const { currentMode, currentPromptMode, isPro, gatedModes } = {
      currentMode: convStore.currentMode,
      currentPromptMode: convStore.currentPromptMode,
      isPro: chatStore.isPro,
      gatedModes: chatStore.gatedModes,
    };

    const requestedMode = options?.mode ?? currentMode;
    const requestedPromptMode = options?.promptMode ?? currentPromptMode;

    if (isModeGated(requestedMode, isPro, gatedModes)) {
      chatStore.openUpgradeModal();
      return;
    }

    const now = new Date().toISOString();
    const localUserId = makeLocalId();
    const clientMessageId = options?.clientMessageId ?? makeClientId();
    const assistantClientId = options?.assistantClientId ?? makeClientId();
    const skipUserMessage = Boolean(options?.skipUserMessage);
    const requestTemperature = Math.min(
      Math.max(options?.temperature ?? 0.7, 0),
      1,
    );

    let conversationId = convStore.currentConversationId;
    let conversation = convStore.conversations.find((c) => c.id === conversationId);
    const effectivePromptMode = isPromptMode(requestedMode)
      ? requestedMode
      : requestedPromptMode;

    convStore.setIsLoading(true);
    convStore.setIsDraftThread(false);

    if (!conversationId && !skipUserMessage) {
      const title = truncateTitle(trimmed);
      const dbConversation = await createConversationInDb(title, requestedMode, effectivePromptMode);
      if (dbConversation) {
        conversation = dbConversation;
        conversationId = dbConversation.id;
        useConversationStore.getState().upsertConversation(conversation);
        convStore.setCurrentConversationId(conversationId);
      }

      if (!conversationId) {
        conversationId = makeLocalId();
        conversation = {
          id: conversationId,
          title: truncateTitle(trimmed),
          mode: requestedMode,
          settings: { mode: requestedMode, prompt_mode: effectivePromptMode },
          created_at: now,
          updated_at: now,
        } as Conversation;
        useConversationStore.getState().upsertConversation(conversation);
        convStore.setCurrentConversationId(conversationId);
      }
    }

    if (!conversationId) {
      notifyError("No active conversation available.");
      convStore.setIsLoading(false);
      return;
    }

    if (!skipUserMessage) {
      const existingUserMessageId = msgStore.messageIds.find((id) => {
        const msg = msgStore.messagesById[id];
        return (
          msg?.clientGeneratedId === clientMessageId ||
          msg?.metadata?.client_id === clientMessageId
        );
      });
      if (!existingUserMessageId) {
        msgStore.addMessage({
          id: localUserId,
          role: "user",
          content: trimmed,
          metadata: {
            client_id: clientMessageId,
            mode: requestedMode,
            prompt_mode: effectivePromptMode,
          },
          created_at: now,
          clientGeneratedId: clientMessageId,
        });
      }
    }

    const updatedNow = new Date().toISOString();
    useConversationStore.setState((state) => ({
      conversations: state.conversations
        .map((c) =>
          c.id === conversationId
            ? { ...c, title: c.title || truncateTitle(trimmed), updated_at: updatedNow }
            : c,
        )
        .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
    }));

    const existingAssistantId =
      options?.replaceMessageId && msgStore.messagesById[options.replaceMessageId]
        ? options.replaceMessageId
        : msgStore.messageIds.find((id) => {
            const msg = msgStore.messagesById[id];
            return (
              msg?.clientGeneratedId === assistantClientId ||
              msg?.metadata?.assistant_client_id === assistantClientId
            );
          });

    if (existingAssistantId) {
      useMessageStore.setState((state) => ({
        messagesById: {
          ...state.messagesById,
          [existingAssistantId]: {
            ...state.messagesById[existingAssistantId],
            content: "",
            isStreaming: true,
            isRegenerating: Boolean(options?.isRegeneration),
            error: undefined,
            syncStatus: "pending",
            clientGeneratedId: assistantClientId,
            metadata: {
              ...state.messagesById[existingAssistantId]?.metadata,
              mode: requestedMode,
              prompt_mode: effectivePromptMode,
              temperature: requestTemperature,
              assistant_client_id: assistantClientId,
            },
          },
        },
      }));
    } else {
      msgStore.addMessage({
        id: makeLocalId(),
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        clientGeneratedId: assistantClientId,
        isStreaming: true,
        isRegenerating: Boolean(options?.isRegeneration),
        syncStatus: "pending",
        metadata: {
          mode: requestedMode,
          prompt_mode: effectivePromptMode,
          temperature: requestTemperature,
          assistant_client_id: assistantClientId,
        },
      });
    }

    const controller = new AbortController();
    const streamStartedAt = Date.now();

    useChatStore.setState((state) => ({
      streamControllers: { ...state.streamControllers, [assistantClientId]: controller },
    }));

    trackTelemetry("message_send", {
      mode: requestedMode,
      prompt_mode: effectivePromptMode,
      regenerate: Boolean(options?.isRegeneration),
    });

    try {
      trackTelemetry("stream_start", { mode: requestedMode });
      let streamError: Error | null = null;

      await sendChat({
        conversationId,
        content: trimmed,
        mode: requestedMode,
        promptMode: effectivePromptMode,
        temperature: requestTemperature,
        isPro,
        isRegeneration: Boolean(options?.isRegeneration),
        clientMessageId,
        assistantClientId,
        signal: controller.signal,
        onChunk: (chunk) => {
          useMessageStore.getState().updateMessageByClientId(
            assistantClientId,
            (msg) => ({ ...msg, content: msg.content + chunk }),
          );
        },
        onServerMessageId: (id) => {
          useMessageStore.getState().updateMessageByClientId(
            assistantClientId,
            (msg) => ({ ...msg, serverMessageId: id }),
          );
        },
        onError: (error) => {
          streamError = error;
        },
        onDone: () => {
          useMessageStore.getState().updateMessageByClientId(assistantClientId, (msg) => ({
            ...msg,
            isStreaming: false,
            isRegenerating: false,
            syncStatus: "synced",
          }));
        },
      });

      if (streamError) {
        throw streamError;
      }

      trackTelemetry("stream_end", {
        status: "success",
        mode: requestedMode,
        duration_ms: Math.max(Date.now() - streamStartedAt, 0),
      });
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) {
        useMessageStore.getState().updateMessageByClientId(assistantClientId, (msg) => ({
          ...msg,
          isStreaming: false,
          isRegenerating: false,
          error: "Canceled",
        }));
        return;
      }

      const apiError = error as ApiError;
      const errorDetail = apiError.detail;
      const retryAllowed = errorDetail?.retry_allowed !== false;
      let errorMessage = getErrorMessage(error, "Failed to send message");
      if (errorDetail?.type === "quota_exceeded") {
        errorMessage = "Daily quota exceeded. Please try again after your quota resets.";
      }
      if (/timed out/i.test(errorMessage)) errorMessage = "Streaming timed out. Retry.";
      if (/duplicate request already in progress/i.test(errorMessage)) {
        errorMessage = "Retry will send a new request.";
      }

      captureFrontendError(
        error instanceof Error ? error : new Error(String(error)),
        { source: "chat.send_message", mode: requestedMode },
      );
      notifyError(errorMessage);

      if (retryAllowed) {
        cachePendingSync({
          id: assistantClientId,
          content: trimmed,
          mode: requestedMode,
          promptMode: effectivePromptMode,
          createdAt: new Date().toISOString(),
          clientMessageId,
          assistantClientId,
        });
      }

      useMessageStore.getState().updateMessageByClientId(assistantClientId, (msg) => ({
        ...msg,
        isStreaming: false,
        isRegenerating: false,
        error: errorMessage,
        syncStatus: "failed",
        retryPayload: retryAllowed
          ? {
              content: trimmed,
              mode: requestedMode,
              promptMode: effectivePromptMode,
              temperature: requestTemperature,
              clientMessageId,
              assistantClientId,
            }
          : undefined,
      }));
    } finally {
      controller.abort();
      useChatStore.setState((state) => {
        return {
          streamControllers: Object.fromEntries(
            Object.entries(state.streamControllers).filter(
              ([key]) => key !== assistantClientId,
            ),
          ),
        };
      });
      const stillStreaming = useMessageStore
        .getState()
        .messageIds.some((id) => useMessageStore.getState().messagesById[id]?.isStreaming);
      useConversationStore.getState().setIsLoading(stillStreaming);
    }
  };

  const regenerateMessage = async (messageId: string, mode?: ChatMode) => {
    const chatStore = useChatStore.getState();
    if (chatStore.regeneratingMessageId) return;

    const { messageIds, messagesById } = useMessageStore.getState();
    const { currentMode, currentPromptMode } = useConversationStore.getState();
    const targetIndex = messageIds.indexOf(messageId);
    if (targetIndex < 0) {
      notifyError("Unable to find the selected message.");
      return;
    }

    let userMessage;
    for (let i = targetIndex - 1; i >= 0; i--) {
      const candidate = messagesById[messageIds[i]];
      if (candidate?.role === "user") {
        userMessage = candidate;
        break;
      }
    }
    if (!userMessage) {
      notifyError("No user prompt found to regenerate.");
      return;
    }

    chatStore.abortAllStreams();

    const target = messagesById[messageId];
    const nextMode = (target?.metadata?.mode as ChatMode | undefined) ?? mode ?? currentMode;
    const nextPromptMode =
      (target?.metadata?.prompt_mode as PromptMode | undefined) ??
      (isPromptMode(nextMode) ? nextMode : currentPromptMode);
    const originalTemp =
      typeof target?.metadata?.temperature === "number" ? target.metadata.temperature : 0.7;
    const nextTemperature = Math.min(originalTemp + 0.1, 1.0);
    const regeneratedClientId = makeClientId();

    useChatStore.setState({ regeneratingMessageId: messageId });
    try {
      await sendMessage(userMessage.content, {
        mode: nextMode,
        promptMode: nextPromptMode,
        isRegeneration: true,
        temperature: nextTemperature,
        clientMessageId: regeneratedClientId,
        assistantClientId: makeClientId(),
        skipUserMessage: true,
        replaceMessageId: messageId,
      });
    } finally {
      useChatStore.setState({ regeneratingMessageId: null });
    }
  };

  const retrySync = async (messageId: string) => {
    const { messageIds, messagesById } = useMessageStore.getState();
    const messageKey = messageIds.find((id) => {
      const msg = messagesById[id];
      return msg?.clientGeneratedId === messageId || id === messageId;
    });
    const message = messageKey ? messagesById[messageKey] : undefined;
    if (!message?.retryPayload) return;

    removePendingSync(messageId);
    useMessageStore.getState().updateMessageByClientId(
      message.clientGeneratedId || messageId,
      (current) => ({ ...current, syncStatus: "pending", error: undefined }),
    );

    await sendMessage(message.retryPayload.content, {
      mode: message.retryPayload.mode as ChatMode,
      promptMode: message.retryPayload.promptMode,
      temperature: message.retryPayload.temperature,
      clientMessageId: makeClientId(),
      assistantClientId: makeClientId(),
      skipUserMessage: true,
      replaceMessageId: messageKey,
    });
  };

  return { sendMessage, regenerateMessage, retrySync };
}
