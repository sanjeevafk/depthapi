import type { StateCreator } from "zustand";
import type { Level } from "../../types";
import type { ChatMode, Conversation, Message, PromptMode } from "../../types/chat";
import type { ChatState } from "../useChatStore";
import { isPromptMode } from "../../lib/chatModes";
import {
  getModeForWorkspace,
  resolveDepthLevel,
  resolveWorkspaceFromMode,
  supabaseConfigured,
  type DepthLevel,
  type Workspace,
} from "../../lib/chatStoreUtils";
import { supabase } from "../../lib/supabase";
import { useMessageStore } from "../useMessageStore";
import { useConversationStore } from "../useConversationStore";

export type ChatMessagesSlice = Pick<
  ChatState,
  | "conversations"
  | "currentConversationId"
  | "isDraftThread"
  | "isLoading"
  | "workspace"
  | "depthLevel"
  | "currentMode"
  | "currentPromptMode"
  | "selectedLevel"
  | "messagesById"
  | "messageIds"
  | "setMode"
  | "setPromptMode"
  | "setWorkspace"
  | "setDepthLevel"
  | "setSelectedLevel"
  | "syncConversations"
  | "selectConversation"
  | "renameConversation"
  | "deleteConversation"
  | "startNewThread"
  | "addMessage"
  | "updateMessageByClientId"
  | "removeMessageByClientId"
>;

export const createChatMessagesSlice: StateCreator<
  ChatState,
  [],
  [],
  ChatMessagesSlice
> = (_set, get) => ({
  get conversations() {
    return useConversationStore.getState().conversations;
  },
  get currentConversationId() {
    return useConversationStore.getState().currentConversationId;
  },
  get isDraftThread() {
    return useConversationStore.getState().isDraftThread;
  },
  get isLoading() {
    return useConversationStore.getState().isLoading;
  },
  get workspace() {
    return useConversationStore.getState().workspace;
  },
  get depthLevel() {
    return useConversationStore.getState().depthLevel;
  },
  get currentMode() {
    return useConversationStore.getState().currentMode;
  },
  get currentPromptMode() {
    return useConversationStore.getState().currentPromptMode;
  },
  get selectedLevel() {
    return useConversationStore.getState().selectedLevel;
  },
  get messagesById() {
    return useMessageStore.getState().messagesById;
  },
  get messageIds() {
    return useMessageStore.getState().messageIds;
  },

  setMode: (mode: ChatMode) => {
    if (Object.keys(get().streamControllers).length > 0) {
      get().abortAllStreams();
    }

    const convStore = useConversationStore.getState();
    const { currentConversationId, conversations, depthLevel } = convStore;
    const conversation = conversations.find((c) => c.id === currentConversationId);
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
        supabase
          .from("conversations")
          .update({ mode, settings: nextSettings })
          .eq("id", currentConversationId)
          .then(({ error }) => {
            if (error) {
              console.error("Failed to persist mode change:", error);
            }
          });
      }
    }
  },

  setPromptMode: (mode: PromptMode) => {
    const convStore = useConversationStore.getState();
    const { currentConversationId, conversations, depthLevel, workspace, currentMode } = convStore;
    const conversation = conversations.find((c) => c.id === currentConversationId);
    const nextDepthLevel = resolveDepthLevel(mode, depthLevel);
    const nextSettings = conversation?.settings
      ? { ...conversation.settings, prompt_mode: mode }
      : conversation
        ? { prompt_mode: mode }
        : undefined;

    convStore.setWorkspaceState(workspace, currentMode, mode, nextDepthLevel);

    if (currentConversationId && conversation && nextSettings) {
      useConversationStore.setState((state) => ({
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === currentConversationId
            ? { ...c, settings: nextSettings }
            : c,
        ),
      }));

      if (supabaseConfigured && !currentConversationId.startsWith("local-")) {
        supabase
          .from("conversations")
          .update({ settings: nextSettings })
          .eq("id", currentConversationId)
          .then(({ error: err }: { error: unknown }) => {
            if (err) {
              console.error("Failed to persist prompt mode change:", err);
            }
          });
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

  syncConversations: (conversations: Conversation[]) =>
    useConversationStore.getState().syncConversations(conversations),

  selectConversation: (id: string) =>
    useConversationStore.getState().selectConversation(id),

  renameConversation: (id: string, title: string) =>
    useConversationStore.getState().renameConversation(id, title),

  deleteConversation: async (id: string) => {
    get().abortAllStreams();
    await useConversationStore.getState().deleteConversation(id);
  },

  startNewThread: () => {
    const { workspace, depthLevel } = useConversationStore.getState();
    get().abortAllStreams();
    useMessageStore.getState().clearMessages();
    useConversationStore.setState({
      currentConversationId: null,
      isDraftThread: true,
      currentMode: getModeForWorkspace(workspace),
      currentPromptMode: depthLevel as PromptMode,
      selectedLevel: depthLevel as Level,
      isLoading: false,
    });
  },

  addMessage: (msg: Message) => useMessageStore.getState().addMessage(msg),
  updateMessageByClientId: (clientId, updater) =>
    useMessageStore.getState().updateMessageByClientId(clientId, updater),
  removeMessageByClientId: (clientId) =>
    useMessageStore.getState().removeMessageByClientId(clientId),
});
