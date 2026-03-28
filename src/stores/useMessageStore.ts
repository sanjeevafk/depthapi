import { create } from "zustand";
import type { Message } from "../types/chat";
import {
  resolveMessageKey,
  findExistingMessageKey,
  buildMessageRegistry,
} from "../lib/chatStoreUtils";

interface MessageState {
  messagesById: Record<string, Message>;
  messageIds: string[];

  // Actions
  setMessages: (messages: Message[]) => void;
  addMessage: (msg: Message) => void;
  updateMessageByClientId: (
    clientId: string,
    updater: (msg: Message) => Message,
  ) => void;
  removeMessageByClientId: (clientId: string) => void;
  clearMessages: () => void;
}

export const useMessageStore = create<MessageState>((set) => ({
  messagesById: {},
  messageIds: [],

  setMessages: (messages: Message[]) => {
    const { messagesById, messageIds } = buildMessageRegistry(messages);
    set({ messagesById, messageIds });
  },

  addMessage: (msg: Message) => {
    set((state) => {
      const resolvedKey = resolveMessageKey(msg);
      const directMatch = state.messagesById[resolvedKey] ? resolvedKey : null;
      const existingKey = directMatch || findExistingMessageKey(state, msg);

      if (existingKey) {
        const existingMessage = state.messagesById[existingKey];
        const incomingContent = typeof msg.content === "string" ? msg.content : "";
        const keepExistingContent =
          existingMessage.role === "assistant" &&
          typeof existingMessage.content === "string" &&
          existingMessage.content.length > 0 &&
          incomingContent.length === 0;
        const nextMessagesById = {
          ...state.messagesById,
          [existingKey]: {
            ...existingMessage,
            ...msg,
            content: keepExistingContent ? existingMessage.content : incomingContent,
            metadata: {
              ...existingMessage.metadata,
              ...msg.metadata,
            },
          },
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
      const { [messageKey]: removed, ...rest } = state.messagesById;
      void removed;
      return {
        messagesById: rest,
        messageIds: state.messageIds.filter((id) => id !== messageKey),
      };
    });
  },

  clearMessages: () => set({ messagesById: {}, messageIds: [] }),
}));
