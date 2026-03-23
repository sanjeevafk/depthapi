import { create } from "zustand";
import type { Level } from "../types";
import type { ChatMode, Conversation, Message, PromptMode } from "../types/chat";
import { createChatUiSlice } from "./slices/chatUiSlice";
import { createChatMessagesSlice } from "./slices/chatMessagesSlice";
import { createChatStreamingSlice } from "./slices/chatStreamingSlice";
import { useMessageStore } from "./useMessageStore";
import { useConversationStore } from "./useConversationStore";
import {
  DEPTH_LEVELS,
  type Workspace,
  type ThemeMode,
  type DepthLevel,
} from "../lib/chatStoreUtils";

export type { Workspace, ThemeMode, DepthLevel };
export { DEPTH_LEVELS };

// ─── Store interface ────────────────────────────────────────────────────────

export interface ChatState {
  // UI state
  theme: ThemeMode;
  isSidebarOpen: boolean;
  isPro: boolean;
  gatedModes: ChatMode[];
  upgradeModalOpen: boolean;
  regenerationModalOpen: boolean;
  regenerationTargetId: string | null;
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
  openRegenerationModal: (messageId: string) => void;
  closeRegenerationModal: () => void;
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
  selectConversation: (id: string) => Promise<void>;
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

export const useChatStore = create<ChatState>((...args) => ({
  ...createChatUiSlice(...args),
  ...createChatMessagesSlice(...args),
  ...createChatStreamingSlice(...args),
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
