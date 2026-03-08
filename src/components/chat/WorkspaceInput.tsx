import { useMemo, useRef, useState } from "react";
import { ArrowRight, Paperclip } from "lucide-react";
import DepthDropdown from "./DepthDropdown";
import { useChatStore } from "../../stores/useChatStore";
import type { DepthLevel, Workspace } from "../../stores/useChatStore";
import type { ChatMode, PromptMode } from "../../types/chat";

interface WorkspaceInputProps {
  workspace: Workspace;
  depthLevel: DepthLevel;
  onDepthChange: (level: DepthLevel) => void;
}

const WORKSPACE_PLACEHOLDERS: Record<Workspace, string> = {
  learn: "What would you like to learn?",
  socratic: "What should we explore through questions?",
  technical: "Ask for a technical deep dive...",
};

const MODE_BY_WORKSPACE: Record<Workspace, ChatMode> = {
  learn: "ensemble",
  socratic: "socratic",
  technical: "technical-depth",
};

export default function WorkspaceInput({
  workspace,
  depthLevel,
  onDepthChange,
}: WorkspaceInputProps): JSX.Element {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const sendMessage = useChatStore((state) => state.sendMessage);
  const isLoading = useChatStore((state) => state.isLoading);
  const currentPromptMode = useChatStore((state) => state.currentPromptMode);

  const isSendDisabled = isLoading || value.trim().length === 0;

  const placeholder = useMemo(
    () => WORKSPACE_PLACEHOLDERS[workspace],
    [workspace],
  );

  const handleSend = async () => {
    if (isSendDisabled) return;
    const content = value.trim();
    if (!content) return;

    setValue("");

    const mode = MODE_BY_WORKSPACE[workspace];
    const promptMode: PromptMode =
      workspace === "learn" ? (depthLevel as PromptMode) : currentPromptMode;

    await sendMessage(content, {
      mode,
      promptMode,
    });

    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  return (
    <div className="sticky bottom-0 z-20 bg-gradient-to-t from-slate-100/95 via-slate-100/70 to-transparent px-4 pb-4 pt-8 dark:from-dark-900/95 dark:via-dark-900/70 sm:px-6 sm:pb-6">
      <div className="mx-auto w-full max-w-3xl">
        <div className="rounded-3xl border border-slate-300 bg-white/90 p-4 shadow-[0_20px_45px_rgba(15,23,42,0.08)] backdrop-blur dark:border-white/10 dark:bg-dark-800/85 dark:shadow-[0_20px_60px_rgba(0,0,0,0.45)]">
          <textarea
            ref={textareaRef}
            value={value}
            rows={2}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSend();
              }
            }}
            placeholder={placeholder}
            aria-label="Message input"
            className="w-full resize-none bg-transparent text-base text-slate-700 placeholder:text-slate-400 focus:outline-none dark:text-slate-200 dark:placeholder:text-slate-500"
          />

          <div className="mt-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                aria-label="Attach file"
                disabled
                className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-300 bg-white text-slate-400 dark:border-white/10 dark:bg-dark-700 dark:text-slate-500"
              >
                <Paperclip className="h-4 w-4" />
              </button>

              {workspace === "learn" && (
                <DepthDropdown value={depthLevel} onChange={onDepthChange} />
              )}
            </div>

            <button
              type="button"
              aria-label="Send message"
              onClick={() => void handleSend()}
              disabled={isSendDisabled}
              className={`inline-flex h-11 w-11 items-center justify-center rounded-2xl transition ${
                isSendDisabled
                  ? "cursor-not-allowed bg-slate-300 text-slate-500 dark:bg-white/10 dark:text-slate-500"
                  : "bg-teal-600 text-white hover:bg-teal-500"
              }`}
            >
              {isLoading ? (
                <span className="h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
              ) : (
                <ArrowRight className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
        <p className="pt-3 text-center text-xs text-slate-500 dark:text-slate-500">
          KnowBear can make mistakes. Verify critical information.
        </p>
      </div>
    </div>
  );
}
