import { useChatStore } from "../stores/useChatStore";

export function useMessageSender() {
  const sendMessage = useChatStore((state) => state.sendMessage);
  const regenerateMessage = useChatStore((state) => state.regenerateMessage);
  const retrySync = useChatStore((state) => state.retrySync);

  return { sendMessage, regenerateMessage, retrySync };
}
