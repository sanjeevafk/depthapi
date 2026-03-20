import { useMemo } from "react";
import { RefreshCcw, X } from "lucide-react";
import { useChatStore } from "../../stores/useChatStore";

export default function RegenerationModal(): JSX.Element | null {
  const isOpen = useChatStore((state) => state.regenerationModalOpen);
  const targetId = useChatStore((state) => state.regenerationTargetId);
  const regeneratingMessageId = useChatStore(
    (state) => state.regeneratingMessageId,
  );
  const messageIds = useChatStore((state) => state.messageIds);
  const messagesById = useChatStore((state) => state.messagesById);
  const regenerateMessage = useChatStore((state) => state.regenerateMessage);
  const closeRegenerationModal = useChatStore(
    (state) => state.closeRegenerationModal,
  );

  const isRegeneratingCurrent = Boolean(
    targetId && regeneratingMessageId === targetId,
  );
  const isAnyRegenerating = Boolean(regeneratingMessageId);

  const userPrompt = useMemo(() => {
    if (!targetId) return "";
    const targetIndex = messageIds.indexOf(targetId);
    if (targetIndex < 0) return "";
    for (let i = targetIndex - 1; i >= 0; i -= 1) {
      const candidate = messagesById[messageIds[i]];
      if (candidate?.role === "user") return candidate.content;
    }
    return "";
  }, [messageIds, messagesById, targetId]);

  if (!isOpen) return null;

  const handleConfirm = async () => {
    if (!targetId || isAnyRegenerating) {
      return;
    }
    await regenerateMessage(targetId);
    closeRegenerationModal();
  };

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={closeRegenerationModal}
      />
      <div className="relative w-full max-w-lg rounded-2xl border border-white/10 bg-dark-800 p-6 shadow-2xl">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-lg font-semibold text-white">
              Regenerate Response
            </h3>
            <p className="text-sm text-gray-400 mt-1">
              Regenerate this reply with the same mode and context.
            </p>
          </div>
          <button
            onClick={closeRegenerationModal}
            disabled={isAnyRegenerating}
            className="text-gray-500 hover:text-white transition"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {userPrompt && (
          <div className="mt-4 rounded-xl border border-white/10 bg-dark-900/60 p-3 text-xs text-gray-300">
            <div className="text-[10px] uppercase tracking-[0.2em] text-gray-500 mb-2">
              Original Prompt
            </div>
            <p className="line-clamp-3">{userPrompt}</p>
          </div>
        )}

        <div className="mt-5 rounded-xl border border-white/10 bg-dark-900/40 px-3 py-2 text-sm text-gray-300">
          Regeneration reuses the original user prompt, conversation context,
          and model alias.
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            onClick={closeRegenerationModal}
            disabled={isAnyRegenerating}
            className="px-4 py-2 text-sm text-gray-400 hover:text-white"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleConfirm()}
            disabled={isAnyRegenerating}
            className="px-4 py-2 rounded-xl text-sm font-semibold flex items-center gap-2 bg-cyan-600 text-white hover:bg-cyan-500 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <RefreshCcw
              className={`h-4 w-4 ${isRegeneratingCurrent ? "animate-spin" : ""}`}
            />
            {isRegeneratingCurrent ? "Regenerating..." : "Regenerate"}
          </button>
        </div>
      </div>
    </div>
  );
}
