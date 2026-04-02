import type { StateCreator } from "zustand";

import type { ChatState } from "../useChatStore";
import { useMessageStore } from "../useMessageStore";
import { useConversationStore } from "../useConversationStore";
export type ChatStreamingSlice = Pick<
  ChatState,
  | "streamControllers"
  | "regeneratingMessageId"
  | "abortStream"
  | "abortAllStreams"
>;

export const createChatStreamingSlice: StateCreator<
  ChatState,
  [],
  [],
  ChatStreamingSlice
> = (set, get) => ({
  streamControllers: {},
  regeneratingMessageId: null,

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
});
