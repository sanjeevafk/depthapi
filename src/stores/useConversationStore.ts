import { create } from "zustand";
import { supabase } from "../lib/supabase";
import type { ChatMode, Conversation, Message, PromptMode } from "../types/chat";
import type { Level } from "../types";
import {
  asString,
  notifyError,
  resolveWorkspaceState,
  loadLastConversationId,
  persistLastConversationId,
  type Workspace,
  type DepthLevel,
  DEFAULT_DEPTH_LEVEL,
} from "../lib/chatStoreUtils";
import { useMessageStore } from "./useMessageStore";

const supabaseConfigured =
  Boolean(import.meta.env.VITE_SUPABASE_URL) &&
  Boolean(import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY);
let latestMessageLoadToken = 0;

interface ConversationState {
  conversations: Conversation[];
  currentConversationId: string | null;
  isDraftThread: boolean;
  isLoading: boolean;

  // Derived workspace state (kept here because it derives from active conversation)
  workspace: Workspace;
  depthLevel: DepthLevel;
  currentMode: ChatMode;
  currentPromptMode: PromptMode;
  selectedLevel: Level;

  // Actions
  syncConversations: (conversations: Conversation[]) => void;
  selectConversation: (
    id: string,
    options?: { forceReload?: boolean },
  ) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  setWorkspaceState: (
    workspace: Workspace,
    mode: ChatMode,
    promptMode: PromptMode,
    depthLevel: DepthLevel,
  ) => void;
  setCurrentConversationId: (id: string | null) => void;
  setIsDraftThread: (draft: boolean) => void;
  setIsLoading: (loading: boolean) => void;
  upsertConversation: (conversation: Conversation) => void;
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  conversations: [],
  currentConversationId: null,
  isDraftThread: false,
  isLoading: false,
  workspace: "learn",
  depthLevel: DEFAULT_DEPTH_LEVEL,
  currentMode: "learn",
  currentPromptMode: DEFAULT_DEPTH_LEVEL as PromptMode,
  selectedLevel: DEFAULT_DEPTH_LEVEL as Level,

  setWorkspaceState: (workspace, mode, promptMode, depthLevel) =>
    set({ workspace, currentMode: mode, currentPromptMode: promptMode, depthLevel, selectedLevel: depthLevel as Level }),

  setCurrentConversationId: (id) => {
    persistLastConversationId(id);
    set({ currentConversationId: id });
  },
  setIsDraftThread: (draft) => set({ isDraftThread: draft }),
  setIsLoading: (loading) => set({ isLoading: loading }),

  upsertConversation: (conversation: Conversation) => {
    set((state) => {
      const exists = state.conversations.some((c) => c.id === conversation.id);
      const next = exists
        ? state.conversations.map((c) =>
            c.id === conversation.id ? conversation : c,
          )
        : [conversation, ...state.conversations];
      return {
        conversations: next.sort((a, b) =>
          a.updated_at < b.updated_at ? 1 : -1,
        ),
      };
    });
  },

  syncConversations: (conversations: Conversation[]) => {
    set((state) => {
      if (state.isDraftThread && state.currentConversationId === null) {
        return { conversations };
      }

      // On startup/login with no active selection, stay in a fresh Learn thread
      // instead of auto-opening the most recent conversation.
      if (state.currentConversationId === null) {
        // If we're currently loading (e.g. sendMessage is mid-flight), 
        // don't reset to the empty state.
        if (state.isLoading) {
          return { conversations };
        }
        const mostRecentId = conversations[0]?.id ?? null;
        if (!mostRecentId) {
          persistLastConversationId(null);
          return {
            conversations,
            currentConversationId: null,
            isDraftThread: true,
            workspace: "learn" as Workspace,
            depthLevel: DEFAULT_DEPTH_LEVEL,
            currentMode: "learn" as ChatMode,
            currentPromptMode: DEFAULT_DEPTH_LEVEL as PromptMode,
            selectedLevel: DEFAULT_DEPTH_LEVEL as Level,
          };
        }

        const activeConversation = conversations.find((item) => item.id === mostRecentId);
        const conversationMode =
          asString(activeConversation?.mode) ||
          asString(activeConversation?.settings?.mode);
        const conversationPrompt =
          asString(activeConversation?.settings?.prompt_mode) ||
          asString(activeConversation?.settings?.mode) ||
          asString(activeConversation?.mode);
        const nextWorkspaceState = resolveWorkspaceState(
          conversationMode,
          conversationPrompt,
          state.depthLevel,
        );
        persistLastConversationId(mostRecentId);
        return {
          conversations,
          currentConversationId: mostRecentId,
          isDraftThread: false,
          workspace: nextWorkspaceState.workspace,
          depthLevel: nextWorkspaceState.depthLevel,
          currentMode: nextWorkspaceState.mode,
          currentPromptMode: nextWorkspaceState.promptMode,
          selectedLevel: nextWorkspaceState.depthLevel as Level,
        };
      }

      const preferredId = state.currentConversationId;
      const hasPreferred = preferredId
        ? conversations.some((item) => item.id === preferredId)
        : false;
      const nextConversationId = hasPreferred
        ? preferredId
        : (conversations[0]?.id ?? null);
      persistLastConversationId(nextConversationId);
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

  selectConversation: async (id: string, options) => {
    if (!id) return;
    const state = get();
    const forceReload = options?.forceReload === true;
    persistLastConversationId(id);

    if (
      state.currentConversationId === id &&
      !forceReload &&
      (state.isLoading || useMessageStore.getState().messageIds.length > 0)
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

    const loadToken = ++latestMessageLoadToken;
    useMessageStore.getState().clearMessages();
    set({
      currentConversationId: id,
      isDraftThread: false,
      isLoading: true,
      workspace: nextWorkspaceState.workspace,
      depthLevel: nextWorkspaceState.depthLevel,
      currentMode: nextWorkspaceState.mode,
      currentPromptMode: nextWorkspaceState.promptMode,
      selectedLevel: nextWorkspaceState.depthLevel as Level,
    });

    const canFetchRemoteMessages =
      !id.startsWith("local-") &&
      typeof (supabase as { from?: unknown })?.from === "function";

    if (!canFetchRemoteMessages) {
      if (loadToken === latestMessageLoadToken) {
        set({ isLoading: false });
      }
      return;
    }

    try {
      const { data, error } = await supabase
        .from("messages")
        .select("id, role, content, attachments, metadata, created_at")
        .eq("conversation_id", id)
        .order("created_at", { ascending: true });

      if (error) throw error;
      if (loadToken !== latestMessageLoadToken || get().currentConversationId !== id) {
        return;
      }
      useMessageStore.getState().setMessages((data ?? []) as Message[]);
    } catch (error) {
      console.error("Failed to fetch messages:", error);
    } finally {
      if (loadToken === latestMessageLoadToken) {
        set({ isLoading: false });
      }
    }
  },

  renameConversation: async (id: string, title: string) => {
    if (!id) return;
    const trimmed = title.trim();
    if (!trimmed) return;

    const existingConversation = get().conversations.find((item) => item.id === id);
    if (!existingConversation) return;
    const previousTitle = existingConversation.title;
    const previousUpdatedAt = existingConversation.updated_at;

    const now = new Date().toISOString();
    set((state) => ({
      conversations: state.conversations
        .map((item) =>
          item.id === id ? { ...item, title: trimmed, updated_at: now } : item,
        )
        .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
    }));

    if (!supabaseConfigured || id.startsWith("local-")) return;

    const rollbackRename = () => {
      set((state) => ({
        conversations: state.conversations
          .map((item) =>
            item.id === id
              ? { ...item, title: previousTitle, updated_at: previousUpdatedAt }
              : item,
          )
          .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
      }));
    };

    try {
      const { data, error } = await supabase
        .from("conversations")
        .update({ title: trimmed, updated_at: now })
        .eq("id", id);

      void data;
      if (error) {
        console.error("Failed to rename conversation:", {
          id,
          title: trimmed,
          error,
        });
        rollbackRename();
        notifyError("Failed to rename conversation.");
      }
    } catch (error) {
      console.error("Failed to rename conversation:", error);
      rollbackRename();
      notifyError("Failed to rename conversation.");
    }
  },

  deleteConversation: async (id: string) => {
    if (!id) return;

    const state = get();
    const targetConversation = state.conversations.find((c) => c.id === id);
    if (!targetConversation) return;
    const isActive = state.currentConversationId === id;

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

    const remaining = get()
      .conversations.filter((c) => c.id !== id)
      .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));

    if (!isActive) {
      set({ conversations: remaining });
      return;
    }

    if (remaining.length === 0) {
      set({ conversations: remaining });
      useMessageStore.getState().clearMessages();
      persistLastConversationId(null);
      set({
        currentConversationId: null,
        isDraftThread: true,
        isLoading: false,
      });
      return;
    }

    const nextId = remaining[0].id;
    persistLastConversationId(nextId);
    set({
      conversations: remaining,
      currentConversationId: nextId,
      isDraftThread: false,
      isLoading: false,
    });
    useMessageStore.getState().clearMessages();
    await get().selectConversation(nextId);
  },
}));
