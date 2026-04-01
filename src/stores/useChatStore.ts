import { create } from "zustand";
import { supabase } from "../lib/supabase";
import type { Level } from "../types";
import type { ChatMode, Conversation, Message, PromptMode } from "../types/chat";
import {
  CHAT_PREMIUM_MODES,
  isModeGated,
  isPromptMode,
} from "../lib/chatModes";
import {
  captureFrontendError,
  trackTelemetry,
} from "../lib/monitoring";
import { sendChat } from "../services/chatService";
import type { ApiError } from "../lib/httpErrors";
import {
  makeLocalId,
  makeClientId,
  truncateTitle,
  notifyError,
  isAbortError,
  getErrorMessage,
  resolveDepthLevel,
  resolveWorkspaceFromMode,
  getModeForWorkspace,
  supabaseConfigured,
  defaultIsPro,
  DEPTH_LEVELS,
  PENDING_SYNC_KEY,
  loadTheme,
  applyThemeClass,
  persistTheme,
  type Workspace,
  type ThemeMode,
  type DepthLevel,
} from "../lib/chatStoreUtils";
import { useMessageStore } from "./useMessageStore";
import { useConversationStore } from "./useConversationStore";

export type { Workspace, ThemeMode, DepthLevel };
export { DEPTH_LEVELS };

// ─── Pending sync helpers (unchanged from original) ────────────────────────

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
        typeof item.mode === "string" &&
        typeof item.createdAt === "string",
    );
  } catch {
    return [];
  }
};

const savePendingSyncs = (entries: PendingSyncEntry[]) => {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PENDING_SYNC_KEY, JSON.stringify(entries));
};

const cachePendingSync = (entry: PendingSyncEntry) => {
  const existing = loadPendingSyncs();
  const next = [entry, ...existing.filter((i) => i.id !== entry.id)].slice(0, 50);
  savePendingSyncs(next);
};

const removePendingSync = (id: string) => {
  savePendingSyncs(loadPendingSyncs().filter((i) => i.id !== id));
};

// ─── Initial theme ─────────────────────────────────────────────────────────

const initialTheme = loadTheme();
applyThemeClass(initialTheme);

// ─── Store interface ────────────────────────────────────────────────────────

export interface ChatState {
  // UI state
  theme: ThemeMode;
  isSidebarOpen: boolean;
  isPro: boolean;
  gatedModes: ChatMode[];
  upgradeModalOpen: boolean;
  regeneratingMessageId: string | null;
  streamControllers: Record<string, AbortController>;

  // Proxy selectors (read-through to sub-stores for backwards compatibility)
  readonly conversations: Conversation[];
  readonly currentConversationId: string | null;
  readonly isDraftThread: boolean;
  readonly isLoading: boolean;
  readonly workspace: Workspace;
  readonly depthLevel: DepthLevel;
  readonly currentMode: ChatMode;
  readonly currentPromptMode: PromptMode;
  readonly selectedLevel: Level;
  readonly messagesById: Record<string, Message>;
  readonly messageIds: string[];

  // Actions — UI
  setTheme: (theme: ThemeMode) => void;
  toggleTheme: () => void;
  setIsSidebarOpen: (open: boolean) => void;
  setIsPro: (isPro: boolean) => void;
  openUpgradeModal: () => void;
  closeUpgradeModal: () => void;
  abortStream: (clientId: string) => void;
  abortAllStreams: () => void;

  // Actions — workspace/mode (delegate to conversation store)
  setMode: (mode: ChatMode) => void;
  setPromptMode: (mode: PromptMode) => void;
  setWorkspace: (workspace: Workspace) => void;
  setDepthLevel: (level: DepthLevel) => void;
  setSelectedLevel: (level: Level) => void;

  // Actions — conversations (delegate to conversation store)
  syncConversations: (conversations: Conversation[]) => void;
  selectConversation: (
    id: string,
    options?: { forceReload?: boolean },
  ) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  startNewThread: () => void;

  // Actions — messages (delegate to message store)
  addMessage: (msg: Message) => void;
  updateMessageByClientId: (clientId: string, updater: (msg: Message) => Message) => void;
  removeMessageByClientId: (clientId: string) => void;

  // Actions — streaming
  sendMessage: (
    content: string,
    options?: {
      mode?: ChatMode;
      promptMode?: PromptMode;
      isRegeneration?: boolean;
      temperature?: number;
      clientMessageId?: string;
      assistantClientId?: string;
      skipUserMessage?: boolean;
      replaceMessageId?: string;
    },
  ) => Promise<void>;
  regenerateMessage: (messageId: string, mode?: ChatMode) => Promise<void>;
  retrySync: (messageId: string) => Promise<void>;
}

// ─── Store implementation ───────────────────────────────────────────────────

export const useChatStore = create<ChatState>((set, get) => ({
  // UI state
  theme: initialTheme,
  isSidebarOpen: false,
  isPro: defaultIsPro,
  gatedModes: [...CHAT_PREMIUM_MODES],
  upgradeModalOpen: false,
  regeneratingMessageId: null,
  streamControllers: {},

  // Proxy selectors — read directly from sub-stores
  get conversations() { return useConversationStore.getState().conversations; },
  get currentConversationId() { return useConversationStore.getState().currentConversationId; },
  get isDraftThread() { return useConversationStore.getState().isDraftThread; },
  get isLoading() { return useConversationStore.getState().isLoading; },
  get workspace() { return useConversationStore.getState().workspace; },
  get depthLevel() { return useConversationStore.getState().depthLevel; },
  get currentMode() { return useConversationStore.getState().currentMode; },
  get currentPromptMode() { return useConversationStore.getState().currentPromptMode; },
  get selectedLevel() { return useConversationStore.getState().selectedLevel; },
  get messagesById() { return useMessageStore.getState().messagesById; },
  get messageIds() { return useMessageStore.getState().messageIds; },

  // ── UI actions ────────────────────────────────────────────────────────────

  setTheme: (theme: ThemeMode) => {
    applyThemeClass(theme);
    persistTheme(theme);
    set({ theme });
  },

  toggleTheme: () => {
    const next: ThemeMode = get().theme === "dark" ? "light" : "dark";
    get().setTheme(next);
  },

  setIsSidebarOpen: (isSidebarOpen) => set({ isSidebarOpen }),
  setIsPro: (isPro) => set({ isPro }),
  openUpgradeModal: () => set({ upgradeModalOpen: true }),
  closeUpgradeModal: () => set({ upgradeModalOpen: false }),

  abortStream: (clientId: string) => {
    const controller = get().streamControllers[clientId];
    if (controller) controller.abort();
    set((state) => {
      return {
        streamControllers: Object.fromEntries(
          Object.entries(state.streamControllers).filter(([key]) => key !== clientId),
        ),
      };
    });
    useMessageStore.getState().updateMessageByClientId(clientId, (msg) => ({
      ...msg,
      isStreaming: false,
      error: "Canceled",
    }));
    const stillStreaming = useMessageStore
      .getState()
      .messageIds.some((id) => useMessageStore.getState().messagesById[id]?.isStreaming);
    useConversationStore.getState().setIsLoading(stillStreaming);
  },

  abortAllStreams: () => {
    const controllers = get().streamControllers;
    Object.values(controllers).forEach((c) => c.abort());
    set({ streamControllers: {} });
    const { messagesById, messageIds } = useMessageStore.getState();
    const updated = { ...messagesById };
    for (const id of messageIds) {
      if (updated[id]?.isStreaming) {
        updated[id] = { ...updated[id], isStreaming: false, error: "Canceled" };
      }
    }
    useMessageStore.setState({ messagesById: updated });
    useConversationStore.getState().setIsLoading(false);
  },

  // ── Workspace/mode actions ────────────────────────────────────────────────

  setMode: (mode: ChatMode) => {
    if (Object.keys(get().streamControllers).length > 0) {
      get().abortAllStreams();
    }

    const convStore = useConversationStore.getState();
    const {
      currentConversationId,
      conversations,
      depthLevel,
      workspace: previousWorkspace,
      currentMode: previousMode,
      currentPromptMode: previousPromptMode,
      selectedLevel: previousSelectedLevel,
    } = convStore;
    const conversation = conversations.find((c) => c.id === currentConversationId);
    const previousConversationMode = conversation?.mode;
    const previousConversationSettings = conversation?.settings;
    const nextPromptMode = isPromptMode(mode) ? mode : convStore.currentPromptMode;
    const nextDepthLevel = resolveDepthLevel(nextPromptMode, depthLevel);
    const nextWorkspace = resolveWorkspaceFromMode(mode);
    const nextSettings = conversation?.settings
      ? { ...conversation.settings, mode, prompt_mode: nextPromptMode }
      : conversation
        ? { mode, prompt_mode: nextPromptMode }
        : undefined;

    convStore.setWorkspaceState(nextWorkspace, mode, nextPromptMode, nextDepthLevel);
    useConversationStore.setState((state) => ({
      ...state,
      selectedLevel: nextDepthLevel as Level,
    }));

    if (currentConversationId && conversation && nextSettings) {
      useConversationStore.setState((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === currentConversationId
            ? { ...c, mode, settings: nextSettings }
            : c,
        ),
      }));

      if (supabaseConfigured && !currentConversationId.startsWith("local-")) {
        const targetConversationId = currentConversationId;
        const rollbackConversationMode = previousConversationMode;
        const rollbackConversationSettings = previousConversationSettings;
        void (async () => {
          try {
            const { error } = await supabase
              .from("conversations")
              .update({ mode, settings: nextSettings })
              .eq("id", targetConversationId);
            if (error) throw error;
          } catch (error) {
            console.error("Failed to update conversation mode:", {
              conversationId: targetConversationId,
              mode,
              nextSettings,
              error,
            });
            useConversationStore.setState((state) => ({
              conversations: state.conversations.map((c) =>
                c.id === targetConversationId
                  ? {
                      ...c,
                      mode: rollbackConversationMode ?? c.mode,
                      settings: rollbackConversationSettings ?? c.settings,
                    }
                  : c,
              ),
              workspace: previousWorkspace,
              currentMode: previousMode,
              currentPromptMode: previousPromptMode,
              depthLevel,
              selectedLevel: previousSelectedLevel,
            }));
          }
        })();
      }
    }
  },

  setPromptMode: (mode: PromptMode) => {
    const convStore = useConversationStore.getState();
    const {
      currentConversationId,
      conversations,
      depthLevel,
      workspace,
      currentMode,
      currentPromptMode: previousPromptMode,
      selectedLevel: previousSelectedLevel,
    } = convStore;
    const conversation = conversations.find((c) => c.id === currentConversationId);
    const previousConversationSettings = conversation?.settings;
    const nextDepthLevel = resolveDepthLevel(mode, depthLevel);
    const nextSettings = conversation?.settings
      ? { ...conversation.settings, prompt_mode: mode }
      : conversation
        ? { prompt_mode: mode }
        : undefined;

    convStore.setWorkspaceState(workspace, currentMode, mode, nextDepthLevel);

    if (currentConversationId && conversation && nextSettings) {
      useConversationStore.setState((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === currentConversationId
            ? { ...c, settings: nextSettings }
            : c,
        ),
      }));

      if (supabaseConfigured && !currentConversationId.startsWith("local-")) {
        const targetConversationId = currentConversationId;
        const rollbackConversationSettings = previousConversationSettings;
        void (async () => {
          try {
            const { error } = await supabase
              .from("conversations")
              .update({ settings: nextSettings })
              .eq("id", targetConversationId);
            if (error) throw error;
          } catch (error) {
            console.error("Failed to update conversation prompt mode:", {
              conversationId: targetConversationId,
              promptMode: mode,
              nextSettings,
              error,
            });
            useConversationStore.setState((state) => ({
              conversations: state.conversations.map((c) =>
                c.id === targetConversationId
                  ? {
                      ...c,
                      settings: rollbackConversationSettings ?? c.settings,
                    }
                  : c,
              ),
              currentPromptMode: previousPromptMode,
              depthLevel,
              selectedLevel: previousSelectedLevel,
            }));
          }
        })();
      }
    }
  },

  setWorkspace: (workspace: Workspace) => {
    get().setMode(getModeForWorkspace(workspace));
    if (workspace === "learn") {
      get().setPromptMode(useConversationStore.getState().depthLevel as PromptMode);
    }
  },

  setDepthLevel: (level: DepthLevel) => {
    useConversationStore.getState().setWorkspaceState(
      useConversationStore.getState().workspace,
      useConversationStore.getState().currentMode,
      level as PromptMode,
      level,
    );
    get().setPromptMode(level as PromptMode);
    if (useConversationStore.getState().workspace === "learn") {
      get().setMode("learn");
    }
  },

  setSelectedLevel: (selectedLevel: Level) => {
    useConversationStore.setState((state) => ({ ...state, selectedLevel }));
  },

  // ── Conversation delegates ────────────────────────────────────────────────

  syncConversations: (conversations) =>
    useConversationStore.getState().syncConversations(conversations),

  selectConversation: (id, options) =>
    useConversationStore
      .getState()
      .selectConversation(id, options ?? { forceReload: true }),

  renameConversation: (id, title) =>
    useConversationStore.getState().renameConversation(id, title),

  deleteConversation: async (id) => {
    get().abortAllStreams();
    await useConversationStore.getState().deleteConversation(id);
  },

  startNewThread: () => {
    const { workspace, depthLevel } = useConversationStore.getState();
    get().abortAllStreams();
    useMessageStore.getState().clearMessages();
    useConversationStore.getState().setCurrentConversationId(null);
    useConversationStore.setState({
      isDraftThread: true,
      currentMode: getModeForWorkspace(workspace),
      currentPromptMode: depthLevel as PromptMode,
      selectedLevel: depthLevel as Level,
      isLoading: false,
    });
  },

  // ── Message delegates ─────────────────────────────────────────────────────

  addMessage: (msg) => useMessageStore.getState().addMessage(msg),
  updateMessageByClientId: (clientId, updater) =>
    useMessageStore.getState().updateMessageByClientId(clientId, updater),
  removeMessageByClientId: (clientId) =>
    useMessageStore.getState().removeMessageByClientId(clientId),

  // ── Streaming ─────────────────────────────────────────────────────────────

  sendMessage: async (content, options) => {
    const trimmed = content.trim();
    if (!trimmed) return;

    const convStore = useConversationStore.getState();
    const msgStore = useMessageStore.getState();
    const { currentMode, currentPromptMode, isPro, gatedModes } = {
      currentMode: convStore.currentMode,
      currentPromptMode: convStore.currentPromptMode,
      isPro: get().isPro,
      gatedModes: get().gatedModes,
    };

    const requestedMode = options?.mode ?? currentMode;
    const requestedPromptMode = options?.promptMode ?? currentPromptMode;

    if (isModeGated(requestedMode, isPro, gatedModes)) {
      get().openUpgradeModal();
      return;
    }

    const now = new Date().toISOString();
    const localUserId = makeLocalId();
    const clientMessageId = options?.clientMessageId ?? makeClientId();
    const assistantClientId = options?.assistantClientId ?? makeClientId();
    const skipUserMessage = Boolean(options?.skipUserMessage);
    const requestTemperature = Math.min(Math.max(options?.temperature ?? 0.7, 0), 1);

    let conversationId = convStore.currentConversationId;
    let conversation = convStore.conversations.find((c) => c.id === conversationId);
    const effectivePromptMode = isPromptMode(requestedMode)
      ? requestedMode
      : requestedPromptMode;

    convStore.setIsLoading(true);
    convStore.setIsDraftThread(false);

    // Create conversation if needed
    if (!conversationId && !skipUserMessage) {
      const title = truncateTitle(trimmed);
      if (supabaseConfigured) {
        try {
          const { data: authData } = await supabase.auth.getUser();
          if (authData?.user) {
            const { data, error } = await supabase
              .from("conversations")
              .insert({
                user_id: authData.user.id,
                title,
                mode: requestedMode,
                settings: { mode: requestedMode, prompt_mode: effectivePromptMode },
              })
              .select("id, title, mode, settings, created_at, updated_at")
              .single();
            if (error) throw error;
            if (data) {
              conversation = data as Conversation;
              conversationId = data.id;
              useConversationStore.getState().upsertConversation(conversation);
              convStore.setCurrentConversationId(conversationId);
            }
          }
        } catch (err) {
          console.error("Failed to create conversation:", err);
        }
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
        };
        useConversationStore.getState().upsertConversation(conversation);
        convStore.setCurrentConversationId(conversationId);
      }
    }

    if (!conversationId) {
      notifyError("No active conversation available.");
      convStore.setIsLoading(false);
      return;
    }

    // Optimistic user message
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

    // Update conversation updated_at
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

    // Optimistic assistant placeholder
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

    set((state) => ({
      streamControllers: { ...state.streamControllers, [assistantClientId]: controller },
    }));

    trackTelemetry("message_send", {
      mode: requestedMode,
      prompt_mode: effectivePromptMode,
      regenerate: Boolean(options?.isRegeneration),
    });

    // ── Execute stream ───────────────────────────────────────────────────────

    try {
      trackTelemetry("stream_start", { mode: requestedMode });
      let streamError: Error | null = null;

      const history = (() => {
        const { messagesById, messageIds } = useMessageStore.getState();
        const maxHistory = 24;
        const baseHistory = messageIds
          .map((id) => messagesById[id])
          .filter((msg) => msg && typeof msg.content === "string")
          .filter((msg) => {
            if (!msg) return false;
            if (msg.clientGeneratedId === assistantClientId) return false;
            if (msg.metadata?.assistant_client_id === assistantClientId) return false;
            return msg.content.trim().length > 0;
          })
          .slice(-maxHistory)
          .map((msg) => ({ role: msg.role, content: msg.content }));

        const last = baseHistory[baseHistory.length - 1];
        if (!skipUserMessage && trimmed) {
          if (!last || last.role !== "user" || last.content !== trimmed) {
            baseHistory.push({ role: "user", content: trimmed });
          }
        }

        if (baseHistory.length > maxHistory) {
          return baseHistory.slice(-maxHistory);
        }

        return baseHistory;
      })();

      await sendChat({
        conversationId,
        content: trimmed,
        mode: requestedMode,
        promptMode: effectivePromptMode,
        temperature: requestTemperature,
        isPro,
        history,
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
          const stillStreaming = useMessageStore
            .getState()
            .messageIds.some((id) => useMessageStore.getState().messagesById[id]?.isStreaming);
          useConversationStore.getState().setIsLoading(stillStreaming);
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
          error: "Request canceled.",
        }));
        return;
      }

      const apiError = error as ApiError;
      const errorDetail = apiError.detail;
      const retryAllowed = errorDetail?.retry_allowed !== false;
      let errorMessage = getErrorMessage(
        error,
        "Request failed. Please try again.",
      );
      if (errorDetail?.type === "quota_exceeded") {
        errorMessage =
          "Rate limited for today. Please try again after your quota resets.";
      }
      if (/timed out/i.test(errorMessage)) {
        errorMessage = "The response timed out. Please retry.";
      }
      if (/duplicate request already in progress/i.test(errorMessage)) {
        errorMessage = "A similar request is still running. Retry to send a new request.";
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
      set((state) => {
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
  },

  regenerateMessage: async (messageId: string, mode?: ChatMode) => {
    if (get().regeneratingMessageId) return;

    const { messageIds, messagesById } = useMessageStore.getState();
    const { currentMode, currentPromptMode } = useConversationStore.getState();
    const targetIndex = messageIds.indexOf(messageId);
    if (targetIndex < 0) { notifyError("Unable to find the selected message."); return; }

    let userMessage: Message | undefined;
    for (let i = targetIndex - 1; i >= 0; i--) {
      const candidate = messagesById[messageIds[i]];
      if (candidate?.role === "user") { userMessage = candidate; break; }
    }
    if (!userMessage) { notifyError("No user prompt found to regenerate."); return; }

    get().abortAllStreams();

    const target = messagesById[messageId];
    const nextMode = (target?.metadata?.mode as ChatMode | undefined) ?? mode ?? currentMode;
    const nextPromptMode =
      (target?.metadata?.prompt_mode as PromptMode | undefined) ??
      (isPromptMode(nextMode) ? nextMode : currentPromptMode);
    const originalTemp =
      typeof target?.metadata?.temperature === "number" ? target.metadata.temperature : 0.7;
    const nextTemperature = Math.min(originalTemp + 0.1, 1.0);
    set({ regeneratingMessageId: messageId });
    try {
      await get().sendMessage(userMessage.content, {
        mode: nextMode,
        promptMode: nextPromptMode,
        isRegeneration: true,
        temperature: nextTemperature,
        clientMessageId: makeClientId(),
        assistantClientId: makeClientId(),
        skipUserMessage: true,
        replaceMessageId: messageId,
      });
    } finally {
      set({ regeneratingMessageId: null });
    }
  },

  retrySync: async (messageId: string) => {
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

    await get().sendMessage(message.retryPayload.content, {
      mode: message.retryPayload.mode as ChatMode,
      promptMode: message.retryPayload.promptMode,
      temperature: message.retryPayload.temperature,
      clientMessageId: makeClientId(),
      assistantClientId: makeClientId(),
      skipUserMessage: true,
      replaceMessageId: messageKey,
    });
  },
}));

const syncLegacyInjectedSlices = (candidate: Partial<ChatState>) => {
  const conversationPatch: Partial<ReturnType<typeof useConversationStore.getState>> = {};
  if ("conversations" in candidate && candidate.conversations !== undefined) {
    conversationPatch.conversations = candidate.conversations;
  }
  if ("currentConversationId" in candidate && candidate.currentConversationId !== undefined) {
    conversationPatch.currentConversationId = candidate.currentConversationId;
  }
  if ("isDraftThread" in candidate && candidate.isDraftThread !== undefined) {
    conversationPatch.isDraftThread = candidate.isDraftThread;
  }
  if ("isLoading" in candidate && candidate.isLoading !== undefined) {
    conversationPatch.isLoading = candidate.isLoading;
  }
  if ("workspace" in candidate && candidate.workspace !== undefined) {
    conversationPatch.workspace = candidate.workspace;
  }
  if ("depthLevel" in candidate && candidate.depthLevel !== undefined) {
    conversationPatch.depthLevel = candidate.depthLevel;
  }
  if ("currentMode" in candidate && candidate.currentMode !== undefined) {
    conversationPatch.currentMode = candidate.currentMode;
  }
  if ("currentPromptMode" in candidate && candidate.currentPromptMode !== undefined) {
    conversationPatch.currentPromptMode = candidate.currentPromptMode;
  }
  if ("selectedLevel" in candidate && candidate.selectedLevel !== undefined) {
    conversationPatch.selectedLevel = candidate.selectedLevel;
  }
  if (Object.keys(conversationPatch).length > 0) {
    useConversationStore.setState(conversationPatch);
  }

  const messagePatch: Partial<ReturnType<typeof useMessageStore.getState>> = {};
  if ("messagesById" in candidate && candidate.messagesById !== undefined) {
    messagePatch.messagesById = candidate.messagesById;
  }
  if ("messageIds" in candidate && candidate.messageIds !== undefined) {
    messagePatch.messageIds = candidate.messageIds;
  }
  if (Object.keys(messagePatch).length > 0) {
    useMessageStore.setState(messagePatch);
  }
};

const restoreProxyGetters = () => {
  const state = useChatStore.getState() as unknown as Record<string, unknown>;
  Object.defineProperties(state, {
    conversations: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().conversations,
    },
    currentConversationId: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().currentConversationId,
    },
    isDraftThread: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().isDraftThread,
    },
    isLoading: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().isLoading,
    },
    workspace: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().workspace,
    },
    depthLevel: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().depthLevel,
    },
    currentMode: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().currentMode,
    },
    currentPromptMode: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().currentPromptMode,
    },
    selectedLevel: {
      configurable: true,
      enumerable: true,
      get: () => useConversationStore.getState().selectedLevel,
    },
    messagesById: {
      configurable: true,
      enumerable: true,
      get: () => useMessageStore.getState().messagesById,
    },
    messageIds: {
      configurable: true,
      enumerable: true,
      get: () => useMessageStore.getState().messageIds,
    },
  });
};

restoreProxyGetters();
useChatStore.subscribe((state) => {
  syncLegacyInjectedSlices(state);
  restoreProxyGetters();
});
