import { create } from "zustand";
import { supabase } from "../lib/supabase";
import type { Session } from "@supabase/supabase-js";
import { splitSseEvents, parseSseEvent } from "../lib/sse";
import { ChatStreamChunkSchema } from "../lib/sseSchemas";
import type { Level } from "../types";
import type {
  ChatMode,
  Conversation,
  Message,
  PromptMode,
} from "../types/chat";
import {
  CHAT_PREMIUM_MODES,
  resolveChatMode,
  isModeGated,
  isPromptMode,
  toQueryLevel,
} from "../lib/chatModes";
import { captureFrontendError, getTracePropagationHeaders, trackTelemetry } from "../lib/monitoring";

export type Workspace = "learn" | "socratic" | "technical";
export type ThemeMode = "dark" | "light";
export const DEPTH_LEVELS = [
  "eli5",
  "eli10",
  "eli12",
  "eli15",
  "meme",
] as const;
export type DepthLevel = (typeof DEPTH_LEVELS)[number];

interface ChatState {
  conversations: Conversation[];
  currentConversationId: string | null;
  isDraftThread: boolean;
  workspace: Workspace;
  depthLevel: DepthLevel;
  theme: ThemeMode;
  currentMode: ChatMode;
  currentPromptMode: PromptMode;
  selectedLevel: Level;
  isSidebarOpen: boolean;
  messagesById: Record<string, Message>;
  messageIds: string[];
  streamControllers: Record<string, AbortController>;
  isLoading: boolean;
  isPro: boolean;
  gatedModes: ChatMode[];
  upgradeModalOpen: boolean;
  regenerationModalOpen: boolean;
  regenerationTargetId: string | null;
  regeneratingMessageId: string | null;
  syncConversations: (conversations: Conversation[]) => void;
  selectConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
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
  setMode: (mode: ChatMode) => void;
  setPromptMode: (mode: PromptMode) => void;
  setWorkspace: (workspace: Workspace) => void;
  setDepthLevel: (level: DepthLevel) => void;
  setSelectedLevel: (level: Level) => void;
  setTheme: (theme: ThemeMode) => void;
  toggleTheme: () => void;
  setIsSidebarOpen: (open: boolean) => void;
  setIsPro: (isPro: boolean) => void;
  startNewThread: () => void;
  openUpgradeModal: () => void;
  closeUpgradeModal: () => void;
  openRegenerationModal: (messageId: string) => void;
  closeRegenerationModal: () => void;
  abortStream: (clientId: string) => void;
  abortAllStreams: () => void;
  addMessage: (msg: Message) => void;
  updateMessageByClientId: (
    clientId: string,
    updater: (msg: Message) => Message,
  ) => void;
  removeMessageByClientId: (clientId: string) => void;
}

const supabaseConfigured =
  Boolean(import.meta.env.VITE_SUPABASE_URL) &&
  Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY);
const defaultIsProEnv = import.meta.env.VITE_DEFAULT_IS_PRO;
const defaultIsPro = defaultIsProEnv ? defaultIsProEnv === "true" : false;
const API_URL = import.meta.env.VITE_API_URL || "";
const THEME_STORAGE_KEY = "kb_theme_v1";
const DEFAULT_WORKSPACE: Workspace = "learn";
const DEFAULT_DEPTH_LEVEL: DepthLevel = "eli12";

const getSupabaseSession = async (): Promise<Session | null> => {
  if (!supabaseConfigured) return null;
  const { data } = await supabase.auth.getSession();
  return data.session;
};

const isAbortError = (error: unknown): boolean => {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: string }).name === "AbortError"
  );
};

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
};

const isDepthLevel = (mode: string | null | undefined): mode is DepthLevel => {
  return DEPTH_LEVELS.includes(mode as DepthLevel);
};

const resolveDepthLevel = (
  mode: string | null | undefined,
  fallback: DepthLevel = DEFAULT_DEPTH_LEVEL,
): DepthLevel => {
  if (isDepthLevel(mode)) return mode;
  return fallback;
};

const resolveWorkspaceFromMode = (mode: ChatMode): Workspace => {
  if (mode === "socratic") return "socratic";
  if (mode === "technical") return "technical";
  return "learn";
};

const resolveWorkspaceState = (
  mode: string | null | undefined,
  promptMode: string | null | undefined,
  fallbackDepth: DepthLevel,
) => {
  const resolvedMode = resolveChatMode(mode);

  if (resolvedMode === "socratic") {
    return {
      workspace: "socratic" as Workspace,
      mode: "socratic" as ChatMode,
      promptMode: resolveDepthLevel(promptMode, fallbackDepth) as PromptMode,
      depthLevel: resolveDepthLevel(promptMode, fallbackDepth),
    };
  }

  if (resolvedMode === "technical") {
    return {
      workspace: "technical" as Workspace,
      mode: "technical" as ChatMode,
      promptMode: resolveDepthLevel(promptMode, fallbackDepth) as PromptMode,
      depthLevel: resolveDepthLevel(promptMode, fallbackDepth),
    };
  }

  const nextDepth = resolveDepthLevel(
    promptMode || (isPromptMode(resolvedMode) ? resolvedMode : undefined),
    fallbackDepth,
  );

  return {
    workspace: "learn" as Workspace,
    mode: "learning" as ChatMode,
    promptMode: nextDepth as PromptMode,
    depthLevel: nextDepth,
  };
};

const loadTheme = (): ThemeMode => {
  if (typeof window === "undefined") return "light";

  const cachedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (cachedTheme === "light" || cachedTheme === "dark") {
    return cachedTheme;
  }

  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
};

const applyThemeClass = (theme: ThemeMode) => {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
};

const persistTheme = (theme: ThemeMode) => {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(THEME_STORAGE_KEY, theme);
};

const getModeForWorkspace = (workspace: Workspace): ChatMode => {
  if (workspace === "socratic") return "socratic";
  if (workspace === "technical") return "technical";
  return "learning";
};

const asString = (value: unknown): string | undefined => {
  return typeof value === "string" ? value : undefined;
};

const initialTheme = loadTheme();
applyThemeClass(initialTheme);

const createUuid = () => {
  const webCrypto: Crypto | undefined =
    typeof globalThis !== "undefined" ? globalThis.crypto : undefined;

  if (webCrypto?.randomUUID) {
    return webCrypto.randomUUID();
  }

  const getRandomValues = webCrypto?.getRandomValues
    ? webCrypto.getRandomValues.bind(webCrypto)
    : null;
  const rnd = (size: number) => {
    if (getRandomValues) {
      const arr = new Uint8Array(size);
      getRandomValues(arr);
      return arr;
    }
    return Uint8Array.from({ length: size }, () =>
      Math.floor(Math.random() * 256),
    );
  };
  const bytes = rnd(16);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex
    .slice(6, 8)
    .join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
};

const makeLocalId = () => `local-${createUuid()}`;

const makeClientId = () => createUuid();

const truncateTitle = (content: string) => {
  const trimmed = content.trim().replace(/\s+/g, " ");
  if (trimmed.length <= 64) return trimmed;
  return `${trimmed.slice(0, 61)}...`;
};

const notifyError = (message: string) => {
  console.error(message);
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("kb-toast", { detail: { type: "error", message } }),
    );
  }
};

const PENDING_SYNC_KEY = "kb_pending_sync_v1";

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
        typeof item.content === "string",
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
  const next = [
    entry,
    ...existing.filter((item) => item.id !== entry.id),
  ].slice(0, 50);
  savePendingSyncs(next);
};

const removePendingSync = (id: string) => {
  const existing = loadPendingSyncs();
  const next = existing.filter((item) => item.id !== id);
  savePendingSyncs(next);
};

const resolveMessageKey = (message: Message) => {
  return (
    message.clientGeneratedId ||
    message.metadata?.assistant_client_id ||
    message.metadata?.client_id ||
    message.serverMessageId ||
    message.id
  );
};

const messagesMatch = (existing: Message, incoming: Message) => {
  if (existing.id === incoming.id) return true;
  if (
    existing.clientGeneratedId &&
    incoming.clientGeneratedId &&
    existing.clientGeneratedId === incoming.clientGeneratedId
  ) {
    return true;
  }
  if (
    incoming.metadata?.assistant_client_id &&
    existing.clientGeneratedId === incoming.metadata.assistant_client_id
  ) {
    return true;
  }
  if (
    existing.metadata?.assistant_client_id &&
    incoming.clientGeneratedId &&
    existing.metadata.assistant_client_id === incoming.clientGeneratedId
  ) {
    return true;
  }
  if (
    existing.serverMessageId &&
    incoming.id &&
    existing.serverMessageId === incoming.id
  )
    return true;
  if (
    incoming.serverMessageId &&
    existing.id &&
    incoming.serverMessageId === existing.id
  )
    return true;
  if (
    incoming.metadata?.client_id &&
    existing.id === incoming.metadata.client_id
  )
    return true;
  if (
    existing.metadata?.client_id &&
    existing.metadata.client_id === incoming.id
  )
    return true;
  if (
    incoming.metadata?.client_id &&
    existing.clientGeneratedId === incoming.metadata.client_id
  )
    return true;
  if (
    existing.metadata?.client_id &&
    incoming.clientGeneratedId &&
    existing.metadata.client_id === incoming.clientGeneratedId
  ) {
    return true;
  }
  if (
    incoming.metadata?.client_id &&
    existing.metadata?.client_id &&
    existing.metadata.client_id === incoming.metadata.client_id
  ) {
    return true;
  }
  return false;
};

const findExistingMessageKey = (
  state: Pick<ChatState, "messagesById" | "messageIds">,
  incoming: Message,
) => {
  for (const messageKey of state.messageIds) {
    const existing = state.messagesById[messageKey];
    if (!existing) continue;
    if (messagesMatch(existing, incoming)) {
      return messageKey;
    }
  }
  return null;
};

const buildMessageRegistry = (messages: Message[]) => {
  const messagesById: Record<string, Message> = {};
  const messageIds: string[] = [];

  for (const message of messages) {
    const key = resolveMessageKey(message);
    if (messagesById[key]) {
      messagesById[key] = { ...messagesById[key], ...message };
      continue;
    }
    messagesById[key] = message;
    messageIds.push(key);
  }

  return { messagesById, messageIds };
};

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  currentConversationId: null,
  isDraftThread: false,
  workspace: DEFAULT_WORKSPACE,
  depthLevel: DEFAULT_DEPTH_LEVEL,
  theme: initialTheme,
  currentMode: "learning",
  currentPromptMode: DEFAULT_DEPTH_LEVEL as PromptMode,
  selectedLevel: DEFAULT_DEPTH_LEVEL as Level,
  isSidebarOpen: false,
  messagesById: {},
  messageIds: [],
  streamControllers: {},
  isLoading: false,
  isPro: defaultIsPro,
  gatedModes: [...CHAT_PREMIUM_MODES],
  upgradeModalOpen: false,
  regenerationModalOpen: false,
  regenerationTargetId: null,
  regeneratingMessageId: null,

  setMode: (mode: ChatMode) => {
    const { currentConversationId, conversations, depthLevel } = get();
    const conversation = conversations.find(
      (item) => item.id === currentConversationId,
    );
    const nextPromptMode = isPromptMode(mode) ? mode : get().currentPromptMode;
    const nextDepthLevel = resolveDepthLevel(nextPromptMode, depthLevel);
    const nextWorkspace = resolveWorkspaceFromMode(mode);
    const nextSettings = conversation?.settings
      ? { ...conversation.settings, mode, prompt_mode: nextPromptMode }
      : conversation
        ? { mode, prompt_mode: nextPromptMode }
        : undefined;

    set((state) => ({
      workspace: nextWorkspace,
      depthLevel: nextDepthLevel,
      currentMode: mode,
      currentPromptMode: nextPromptMode,
      selectedLevel: nextDepthLevel as Level,
      conversations: currentConversationId
        ? state.conversations.map((item) =>
            item.id === currentConversationId
              ? { ...item, mode, settings: nextSettings ?? item.settings }
              : item,
          )
        : state.conversations,
    }));

    if (
      !supabaseConfigured ||
      !currentConversationId ||
      !conversation ||
      currentConversationId.startsWith("local-")
    ) {
      return;
    }

    void supabase
      .from("conversations")
      .update({ mode, settings: nextSettings ?? conversation.settings })
      .eq("id", currentConversationId);
  },

  setPromptMode: (mode: PromptMode) => {
    const { currentConversationId, conversations } = get();
    const conversation = conversations.find(
      (item) => item.id === currentConversationId,
    );
    const nextDepthLevel = resolveDepthLevel(mode, get().depthLevel);
    const nextSettings = conversation?.settings
      ? { ...conversation.settings, prompt_mode: mode }
      : conversation
        ? { prompt_mode: mode }
        : undefined;

    set((state) => ({
      currentPromptMode: mode,
      depthLevel: nextDepthLevel,
      selectedLevel: nextDepthLevel as Level,
      conversations: currentConversationId
        ? state.conversations.map((item) =>
            item.id === currentConversationId
              ? { ...item, settings: nextSettings ?? item.settings }
              : item,
          )
        : state.conversations,
    }));

    if (
      !supabaseConfigured ||
      !currentConversationId ||
      !conversation ||
      currentConversationId.startsWith("local-")
    ) {
      return;
    }

    void supabase
      .from("conversations")
      .update({ settings: nextSettings ?? conversation.settings })
      .eq("id", currentConversationId);
  },

  setWorkspace: (workspace: Workspace) => {
    get().setMode(getModeForWorkspace(workspace));
    if (workspace === "learn") {
      const nextDepth = get().depthLevel;
      get().setPromptMode(nextDepth as PromptMode);
    }
  },

  setDepthLevel: (level: DepthLevel) => {
    set({
      depthLevel: level,
      selectedLevel: level as Level,
    });
    get().setPromptMode(level as PromptMode);
    if (get().workspace === "learn") {
      get().setMode("learning");
    }
  },

  setSelectedLevel: (selectedLevel: Level) => set({ selectedLevel }),

  setTheme: (theme: ThemeMode) => {
    applyThemeClass(theme);
    persistTheme(theme);
    set({ theme });
  },

  toggleTheme: () => {
    const nextTheme: ThemeMode = get().theme === "dark" ? "light" : "dark";
    get().setTheme(nextTheme);
  },

  setIsSidebarOpen: (isSidebarOpen: boolean) => set({ isSidebarOpen }),

  setIsPro: (isPro: boolean) => set({ isPro }),

  startNewThread: () => {
    const { workspace, depthLevel } = get();
    get().abortAllStreams();
    set({
      currentConversationId: null,
      isDraftThread: true,
      currentMode: getModeForWorkspace(workspace),
      currentPromptMode: depthLevel as PromptMode,
      selectedLevel: depthLevel as Level,
      messagesById: {},
      messageIds: [],
      isLoading: false,
    });
  },

  openUpgradeModal: () => set({ upgradeModalOpen: true }),
  closeUpgradeModal: () => set({ upgradeModalOpen: false }),
  openRegenerationModal: (messageId: string) =>
    set({ regenerationModalOpen: true, regenerationTargetId: messageId }),
  closeRegenerationModal: () =>
    set({ regenerationModalOpen: false, regenerationTargetId: null }),
  abortStream: (clientId: string) => {
    const controller = get().streamControllers[clientId];
    if (controller) controller.abort();
    set((state) => {
      const { [clientId]: removedController, ...rest } =
        state.streamControllers;
      void removedController;
      return { streamControllers: rest };
    });
    get().updateMessageByClientId(clientId, (message) => ({
      ...message,
      isStreaming: false,
      error: "Canceled",
    }));
    const stillStreaming = get().messageIds.some(
      (id) => get().messagesById[id]?.isStreaming,
    );
    set({ isLoading: stillStreaming });
  },
  abortAllStreams: () => {
    const controllers = get().streamControllers;
    Object.values(controllers).forEach((controller) => controller.abort());
    set({ streamControllers: {} });
    set((state) => {
      const messagesById = { ...state.messagesById };
      for (const id of state.messageIds) {
        const message = messagesById[id];
        if (message?.isStreaming) {
          messagesById[id] = {
            ...message,
            isStreaming: false,
            error: "Canceled",
          };
        }
      }
      return { messagesById };
    });
    set({ isLoading: false });
  },

  syncConversations: (conversations: Conversation[]) => {
    set((state) => {
      if (state.isDraftThread && state.currentConversationId === null) {
        return { conversations };
      }

      const preferredId = state.currentConversationId;
      const hasPreferred = preferredId
        ? conversations.some((item) => item.id === preferredId)
        : false;
      const nextConversationId = hasPreferred
        ? preferredId
        : (conversations[0]?.id ?? null);
      const activeConversation = conversations.find(
        (item) => item.id === nextConversationId,
      );
      const conversationMode =
        asString(activeConversation?.mode) ||
        asString(activeConversation?.settings?.mode);
      const conversationPrompt =
        asString(activeConversation?.settings?.prompt_mode) ||
        asString(activeConversation?.settings?.mode) ||
        asString(activeConversation?.mode) ||
        state.currentPromptMode;
      const nextWorkspaceState = resolveWorkspaceState(
        conversationMode,
        conversationPrompt,
        state.depthLevel,
      );
      return {
        conversations,
        currentConversationId: nextConversationId,
        isDraftThread: false,
        workspace: nextWorkspaceState.workspace,
        depthLevel: nextWorkspaceState.depthLevel,
        currentMode: nextWorkspaceState.mode,
        currentPromptMode: nextWorkspaceState.promptMode,
        selectedLevel: nextWorkspaceState.depthLevel as Level,
      };
    });
  },

  selectConversation: async (id: string) => {
    if (!id) return;
    const state = get();

    if (
      state.currentConversationId === id &&
      (state.isLoading || state.messageIds.length > 0)
    ) {
      return;
    }

    const activeConversation = state.conversations.find(
      (item) => item.id === id,
    );
    const conversationMode =
      asString(activeConversation?.mode) ||
      asString(activeConversation?.settings?.mode) ||
      state.currentMode;
    const conversationPrompt =
      asString(activeConversation?.settings?.prompt_mode) ||
      asString(activeConversation?.settings?.mode) ||
      asString(activeConversation?.mode) ||
      state.currentPromptMode;
    const nextWorkspaceState = resolveWorkspaceState(
      conversationMode,
      conversationPrompt,
      state.depthLevel,
    );
    set({
      currentConversationId: id,
      isDraftThread: false,
      messagesById: {},
      messageIds: [],
      isLoading: true,
      workspace: nextWorkspaceState.workspace,
      depthLevel: nextWorkspaceState.depthLevel,
      currentMode: nextWorkspaceState.mode,
      currentPromptMode: nextWorkspaceState.promptMode,
      selectedLevel: nextWorkspaceState.depthLevel as Level,
    });

    if (!supabaseConfigured) {
      set({ isLoading: false });
      return;
    }

    try {
      const { data, error } = await supabase
        .from("messages")
        .select("id, role, content, attachments, metadata, created_at")
        .eq("conversation_id", id)
        .order("created_at", { ascending: true });

      if (error) throw error;

      const { messagesById, messageIds } = buildMessageRegistry(
        (data ?? []) as Message[],
      );
      set({ messagesById, messageIds });
    } catch (error) {
      console.error("Failed to fetch messages:", error);
    } finally {
      set({ isLoading: false });
    }
  },

  renameConversation: async (id: string, title: string) => {
    if (!id) return;
    const trimmed = title.trim();
    if (!trimmed) return;

    const now = new Date().toISOString();
    set((state) => ({
      conversations: state.conversations
        .map((item) =>
          item.id === id ? { ...item, title: trimmed, updated_at: now } : item,
        )
        .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
    }));

    if (!supabaseConfigured || id.startsWith("local-")) return;

    try {
      await supabase
        .from("conversations")
        .update({ title: trimmed, updated_at: now })
        .eq("id", id);
    } catch (error) {
      console.error("Failed to rename conversation:", error);
    }
  },

  deleteConversation: async (id: string) => {
    if (!id) return;

    const initialState = get();
    const targetConversation = initialState.conversations.find(
      (conversation) => conversation.id === id,
    );
    if (!targetConversation) return;
    const isActiveConversation = initialState.currentConversationId === id;

    if (isActiveConversation) {
      get().abortAllStreams();
    }

    if (!id.startsWith("local-") && supabaseConfigured) {
      try {
        const { error } = await supabase
          .from("conversations")
          .delete()
          .eq("id", id);
        if (error) throw error;
      } catch (error) {
        console.error("Failed to delete conversation:", error);
        notifyError("Failed to delete conversation.");
        return;
      }
    }

    const state = get();
    const remainingConversations = state.conversations
      .filter((conversation) => conversation.id !== id)
      .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));

    if (!isActiveConversation) {
      set({ conversations: remainingConversations });
      return;
    }

    if (remainingConversations.length === 0) {
      set({ conversations: remainingConversations });
      get().startNewThread();
      return;
    }

    const nextConversationId = remainingConversations[0].id;
    set({
      conversations: remainingConversations,
      currentConversationId: nextConversationId,
      isDraftThread: false,
      messagesById: {},
      messageIds: [],
      isLoading: false,
    });
    await get().selectConversation(nextConversationId);
  },

  addMessage: (msg: Message) => {
    set((state) => {
      const resolvedKey = resolveMessageKey(msg);
      const directMatch = state.messagesById[resolvedKey] ? resolvedKey : null;
      const existingKey = directMatch || findExistingMessageKey(state, msg);

      if (existingKey) {
        const nextMessagesById = {
          ...state.messagesById,
          [existingKey]: { ...state.messagesById[existingKey], ...msg },
        };
        if (!state.messageIds.includes(existingKey)) {
          return {
            messagesById: nextMessagesById,
            messageIds: [...state.messageIds, existingKey],
          };
        }
        return { messagesById: nextMessagesById };
      }

      return {
        messagesById: { ...state.messagesById, [resolvedKey]: msg },
        messageIds: [...state.messageIds, resolvedKey],
      };
    });
  },

  updateMessageByClientId: (
    clientId: string,
    updater: (msg: Message) => Message,
  ) => {
    set((state) => {
      const messageKey = state.messageIds.find((id) => {
        const message = state.messagesById[id];
        if (!message) return false;
        return (
          message.clientGeneratedId === clientId ||
          message.metadata?.assistant_client_id === clientId ||
          message.metadata?.client_id === clientId
        );
      });

      if (!messageKey) return state;
      const message = state.messagesById[messageKey];
      return {
        messagesById: {
          ...state.messagesById,
          [messageKey]: updater(message),
        },
      };
    });
  },

  removeMessageByClientId: (clientId: string) => {
    set((state) => {
      const messageKey = state.messageIds.find((id) => {
        const message = state.messagesById[id];
        if (!message) return false;
        return (
          message.clientGeneratedId === clientId ||
          message.metadata?.assistant_client_id === clientId ||
          message.metadata?.client_id === clientId
        );
      });

      if (!messageKey) return state;

      const { [messageKey]: removedMessage, ...rest } = state.messagesById;
      void removedMessage;
      return {
        messagesById: rest,
        messageIds: state.messageIds.filter((id) => id !== messageKey),
      };
    });
  },

  sendMessage: async (
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
  ) => {
    const trimmed = content.trim();
    if (!trimmed) return;

    const { currentMode, currentPromptMode, isPro, gatedModes } = get();
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
    let conversationId = get().currentConversationId;
    let conversation = get().conversations.find(
      (item) => item.id === conversationId,
    );
    const effectivePromptMode = isPromptMode(requestedMode)
      ? requestedMode
      : requestedPromptMode;

    set({ isLoading: true, isDraftThread: false });

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
                settings: {
                  mode: requestedMode,
                  prompt_mode: effectivePromptMode,
                },
              })
              .select("id, title, mode, settings, created_at, updated_at")
              .single();

            if (error) throw error;

            if (data) {
              conversation = data as Conversation;
              conversationId = data.id;
              set((state) => ({
                conversations: [
                  conversation as Conversation,
                  ...state.conversations,
                ],
                currentConversationId: conversationId,
              }));
            }
          }
        } catch (error) {
          console.error("Failed to create conversation:", error);
        }
      }

      if (!conversationId) {
        conversationId = makeLocalId();
        conversation = {
          id: conversationId,
          title,
          mode: requestedMode,
          settings: { mode: requestedMode, prompt_mode: effectivePromptMode },
          created_at: now,
          updated_at: now,
        };
        set((state) => ({
          conversations: [conversation as Conversation, ...state.conversations],
          currentConversationId: conversationId,
        }));
      }
    }

    if (!conversationId) {
      notifyError("No active conversation available.");
      set({ isLoading: false });
      return;
    }

    const existingUserMessageId = get().messageIds.find((id) => {
      const message = get().messagesById[id];
      if (!message) return false;
      return (
        message.clientGeneratedId === clientMessageId ||
        message.metadata?.client_id === clientMessageId
      );
    });

    if (!skipUserMessage && !existingUserMessageId) {
      const optimisticUserMessage: Message = {
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
      };

      get().addMessage(optimisticUserMessage);
    }
    set((state) => ({
      conversations: state.conversations
        .map((item) =>
          item.id === conversationId
            ? {
                ...item,
                title: item.title || truncateTitle(trimmed),
                updated_at: now,
              }
            : item,
        )
        .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
    }));

    const existingAssistantMessageId =
      options?.replaceMessageId && get().messagesById[options.replaceMessageId]
        ? options.replaceMessageId
        : get().messageIds.find((id) => {
            const message = get().messagesById[id];
            if (!message) return false;
            return (
              message.clientGeneratedId === assistantClientId ||
              message.metadata?.assistant_client_id === assistantClientId
            );
          });

    if (existingAssistantMessageId) {
      set((state) => ({
        messagesById: {
          ...state.messagesById,
          [existingAssistantMessageId]: {
            ...state.messagesById[existingAssistantMessageId],
            content: "",
            isStreaming: true,
            isRegenerating: Boolean(options?.isRegeneration),
            error: undefined,
            syncStatus: "pending",
            clientGeneratedId: assistantClientId,
            metadata: {
              ...state.messagesById[existingAssistantMessageId]?.metadata,
              mode: requestedMode,
              prompt_mode: effectivePromptMode,
              temperature: requestTemperature,
              assistant_client_id: assistantClientId,
            },
          },
        },
      }));
    } else {
      const assistantPlaceholder: Message = {
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
      };

      get().addMessage(assistantPlaceholder);
    }

    const controller = new AbortController();
    const streamStartedAt = Date.now();
    let streamStarted = false;
    set((state) => ({
      streamControllers: {
        ...state.streamControllers,
        [assistantClientId]: controller,
      },
    }));

    trackTelemetry("message_send", {
      mode: requestedMode,
      prompt_mode: effectivePromptMode,
      regenerate: Boolean(options?.isRegeneration),
      retry: Boolean(options?.clientMessageId),
    });

    try {
      const session = await getSupabaseSession();
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (session?.access_token) {
        headers["Authorization"] = `Bearer ${session.access_token}`;
      }
      Object.assign(headers, getTracePropagationHeaders());
      headers["x-request-id"] = createUuid();

      const streamFromResponse = async (
        response: Response,
        handler: (payload: unknown) => void,
      ) => {
        if (!response.body) {
          throw new Error("Streaming not supported in this environment");
        }

        const contentType = response.headers.get("content-type");
        if (contentType && !contentType.includes("text/event-stream")) {
          throw new Error(`Unexpected content-type: ${contentType}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const READ_TIMEOUT_MS = 20000;
        let doneReceived = false;

        while (true) {
          const { value, done } = await Promise.race([
            reader.read(),
            new Promise<ReadableStreamReadResult<Uint8Array>>((_, reject) =>
              setTimeout(
                () => reject(new Error("Stream read timed out")),
                READ_TIMEOUT_MS,
              ),
            ),
          ]);
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const { events, remainder } = splitSseEvents(buffer);
          buffer = remainder;

          for (const eventBlock of events) {
            const parsed = parseSseEvent(eventBlock);
            if (!parsed) {
              console.warn("Skipping invalid SSE event:", eventBlock);
              continue;
            }
            if (parsed.event === "heartbeat") continue;
            if (parsed.event === "done" || parsed.data === "[DONE]") {
              doneReceived = true;
              break;
            }

            let payload: unknown = null;
            try {
              payload = JSON.parse(parsed.data);
            } catch {
              payload = { delta: parsed.data };
            }

            handler(payload);
          }

          if (doneReceived) {
            try {
              await reader.cancel();
            } catch {
              // ignore
            }
            break;
          }
        }
      };

      const buildHttpError = async (response: Response) => {
        let message = "";
        try {
          const payload = (await response.json()) as Record<string, unknown>;
          if (payload && typeof payload === "object") {
            const detail = payload.detail;
            const error = payload.error;
            if (typeof detail === "string" && detail.trim()) {
              message = detail.trim();
            } else if (typeof error === "string" && error.trim()) {
              message = error.trim();
            }
          }
        } catch {
          // ignored: non-JSON error responses
        }

        if (response.status === 429) {
          const retryAfter = response.headers.get("retry-after");
          const suffix = retryAfter
            ? ` Retry after ${retryAfter} seconds.`
            : "";
          const err = new Error(
            `${message || "You are sending requests too quickly."}${suffix}`,
          ) as Error & { status?: number };
          err.status = 429;
          return err;
        }

        const err = new Error(
          message || `Request failed with status ${response.status}`,
        ) as Error & { status?: number };
        err.status = response.status;
        return err;
      };

      const handleStreamingPayload = (
        rawPayload: unknown,
        chunkKey: "delta" | "chunk",
      ) => {
        const parsed = ChatStreamChunkSchema.safeParse(rawPayload);
        if (!parsed.success) {
          console.warn("Skipping invalid SSE payload:", parsed.error);
          return;
        }

        const payload = parsed.data;
        const chunk = payload?.[chunkKey] ?? payload?.delta ?? payload?.chunk;
        if (chunk) {
          get().updateMessageByClientId(assistantClientId, (message) => ({
            ...message,
            content: `${message.content}${chunk}`,
          }));
        }

        const serverMessageId =
          payload?.assistant_message_id || payload?.message_id;
        if (serverMessageId) {
          get().updateMessageByClientId(assistantClientId, (message) => ({
            ...message,
            serverMessageId,
          }));
        }

        if (payload?.error) {
          throw new Error(payload.error);
        }
      };

      const executeStream = async () => {
        streamStarted = true;
        trackTelemetry("stream_start", {
          endpoint: "/api/messages",
          mode: requestedMode,
          regenerate: Boolean(options?.isRegeneration),
        });

        const fallbackToQueryStream = async (reason: "local" | "fallback") => {
          const fallbackLevel = toQueryLevel(effectivePromptMode);
          trackTelemetry("stream_start", {
            endpoint: "/api/query/stream",
            mode: requestedMode,
            regenerate: Boolean(options?.isRegeneration),
            fallback: true,
            reason,
          });
          const fallbackResponse = await fetch(`${API_URL}/api/query/stream`, {
            method: "POST",
            headers,
            signal: controller.signal,
            body: JSON.stringify({
              topic: trimmed,
              levels: [fallbackLevel],
              mode: requestedMode,
              premium: isPro,
              regenerate: Boolean(options?.isRegeneration),
              bypass_cache: Boolean(options?.isRegeneration),
              temperature: requestTemperature,
              message_id: clientMessageId,
            }),
          });

          if (!fallbackResponse.ok) {
            throw await buildHttpError(fallbackResponse);
          }

          await streamFromResponse(fallbackResponse, (payload) =>
            handleStreamingPayload(payload, "chunk"),
          );
          return;
        };

        const shouldUseMessagesEndpoint =
          Boolean(session?.access_token) &&
          supabaseConfigured &&
          !conversationId.startsWith("local-");

        if (!shouldUseMessagesEndpoint) {
          await fallbackToQueryStream("local");
          return;
        }

        let response = await fetch(`${API_URL}/api/messages`, {
          method: "POST",
          headers,
          signal: controller.signal,
          body: JSON.stringify({
            conversation_id: conversationId,
            content: trimmed,
            client_generated_id: clientMessageId,
            assistant_client_id: assistantClientId,
            mode: requestedMode,
            prompt_mode: effectivePromptMode,
            regenerate: Boolean(options?.isRegeneration),
            temperature: requestTemperature,
          }),
        });

        const shouldFallback =
          response.status === 404 || response.status === 405;
        if (shouldFallback) {
          await fallbackToQueryStream("fallback");
          return;
        }

        if (!response.ok) {
          throw await buildHttpError(response);
        }

        await streamFromResponse(response, (payload) =>
          handleStreamingPayload(payload, "delta"),
        );
      };

      await executeStream();
      if (streamStarted) {
        trackTelemetry("stream_end", {
          status: "success",
          mode: requestedMode,
          duration_ms: Math.max(Date.now() - streamStartedAt, 0),
        });
      }

      get().updateMessageByClientId(assistantClientId, (message) => ({
        ...message,
        isStreaming: false,
        isRegenerating: false,
        syncStatus: "synced",
      }));
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) {
        if (streamStarted) {
          trackTelemetry("stream_end", {
            status: "aborted",
            mode: requestedMode,
            duration_ms: Math.max(Date.now() - streamStartedAt, 0),
          });
        }
        get().updateMessageByClientId(assistantClientId, (message) => ({
          ...message,
          isStreaming: false,
          isRegenerating: false,
          error: "Canceled",
        }));
        return;
      }

      let errorMessage = getErrorMessage(error, "Failed to send message");
      if (/timed out/i.test(errorMessage)) {
        errorMessage = "Streaming timed out. Retry.";
      }
      if (streamStarted) {
        trackTelemetry("stream_end", {
          status: "error",
          mode: requestedMode,
          duration_ms: Math.max(Date.now() - streamStartedAt, 0),
          error_type: (error as { name?: string })?.name || "Error",
        });
      }
      captureFrontendError(
        error instanceof Error ? error : new Error(String(error || "Unknown chat send error")),
        {
          source: "chat.send_message",
          mode: requestedMode,
          regenerate: Boolean(options?.isRegeneration),
        },
      );
      notifyError(errorMessage);
      const retryPayload = {
        content: trimmed,
        mode: requestedMode,
        promptMode: effectivePromptMode,
        temperature: requestTemperature,
        clientMessageId,
        assistantClientId,
      };
      cachePendingSync({
        id: assistantClientId,
        content: trimmed,
        mode: requestedMode,
        promptMode: effectivePromptMode,
        createdAt: new Date().toISOString(),
        clientMessageId,
        assistantClientId,
      });

      get().updateMessageByClientId(assistantClientId, (message) => ({
        ...message,
        isStreaming: false,
        isRegenerating: false,
        error: errorMessage || getErrorMessage(error, "Failed to sync message"),
        syncStatus: "failed",
        retryPayload,
      }));
    } finally {
      set((state) => {
        const { [assistantClientId]: removedController, ...rest } =
          state.streamControllers;
        void removedController;
        return { streamControllers: rest };
      });
      const stillStreaming = get().messageIds.some(
        (id) => get().messagesById[id]?.isStreaming,
      );
      set({ isLoading: stillStreaming });
    }
  },

  regenerateMessage: async (messageId: string, mode?: ChatMode) => {
    if (get().regeneratingMessageId) {
      return;
    }

    const { messageIds, messagesById, currentMode, currentPromptMode } = get();
    const targetIndex = messageIds.indexOf(messageId);
    if (targetIndex < 0) {
      notifyError("Unable to find the selected message.");
      return;
    }

    let userMessage: Message | undefined;
    for (let i = targetIndex - 1; i >= 0; i -= 1) {
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

    get().abortAllStreams();

    const targetAssistant = messagesById[messageId];
    const nextMode =
      (targetAssistant?.metadata?.mode as ChatMode | undefined) ??
      mode ??
      currentMode;
    const nextPromptMode =
      (targetAssistant?.metadata?.prompt_mode as PromptMode | undefined) ??
      (isPromptMode(nextMode) ? nextMode : currentPromptMode);
    const originalTemperature =
      typeof targetAssistant?.metadata?.temperature === "number"
        ? targetAssistant.metadata.temperature
        : 0.7;
    const nextTemperature = Math.min(originalTemperature + 0.1, 1.0);
    const originalClientId =
      typeof userMessage.metadata?.client_id === "string"
        ? userMessage.metadata.client_id
        : makeClientId();

    set({ regeneratingMessageId: messageId });

    try {
      await get().sendMessage(userMessage.content, {
        mode: nextMode,
        promptMode: nextPromptMode,
        isRegeneration: true,
        temperature: nextTemperature,
        clientMessageId: originalClientId,
        assistantClientId: makeClientId(),
        skipUserMessage: true,
        replaceMessageId: messageId,
      });
    } finally {
      set({ regeneratingMessageId: null });
    }
  },

  retrySync: async (messageId: string) => {
    const state = get();
    const messageKey = state.messageIds.find((id) => {
      const msg = state.messagesById[id];
      return msg?.clientGeneratedId === messageId || id === messageId;
    });
    const message = messageKey ? state.messagesById[messageKey] : undefined;
    if (!message?.retryPayload) return;

    removePendingSync(messageId);
    get().updateMessageByClientId(
      message.clientGeneratedId || messageId,
      (current) => ({
        ...current,
        syncStatus: "pending",
        error: undefined,
      }),
    );

    await get().sendMessage(message.retryPayload.content, {
      mode: message.retryPayload.mode as ChatMode,
      promptMode: message.retryPayload.promptMode,
      temperature: message.retryPayload.temperature,
      clientMessageId: message.retryPayload.clientMessageId,
      assistantClientId: message.retryPayload.assistantClientId,
    });
  },
}));
