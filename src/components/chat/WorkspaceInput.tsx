import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Paperclip } from "lucide-react";
import DepthDropdown from "./DepthDropdown";
import { useChatStore } from "../../stores/useChatStore";
import type { DepthLevel, Workspace } from "../../lib/chatStoreUtils";
import type { ChatMode, PromptMode } from "../../types/chat";

interface WorkspaceInputProps {
  workspace: Workspace;
  depthLevel: DepthLevel;
  onDepthChange: (level: DepthLevel) => void;
  disabled?: boolean;
  disabledReason?: string;
}

const WORKSPACE_PLACEHOLDERS: Record<Workspace, string> = {
  learn: "What would you like to learn?",
  socratic: "What should we explore through questions?",
  technical: "Ask for a technical deep dive...",
};

const MODE_BY_WORKSPACE: Record<Workspace, ChatMode> = {
  learn: "learn",
  socratic: "socratic",
  technical: "technical",
};

export default function WorkspaceInput({
  workspace,
  depthLevel,
  onDepthChange,
  disabled = false,
  disabledReason,
}: WorkspaceInputProps): JSX.Element {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const baseHeightRef = useRef<number | null>(null);
  const sendMessage = useChatStore((state) => state.sendMessage);
  const isLoading = useChatStore((state) => state.isLoading);
  const currentPromptMode = useChatStore((state) => state.currentPromptMode);
  const MAX_TEXTAREA_HEIGHT = 180;

  const isSendDisabled = disabled || isLoading || value.trim().length === 0;

  const placeholder = useMemo(
    () =>
      disabled && disabledReason
        ? disabledReason
        : WORKSPACE_PLACEHOLDERS[workspace],
    [disabled, disabledReason, workspace],
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

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    if (baseHeightRef.current === null) {
      baseHeightRef.current = textarea.scrollHeight;
    }
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, MAX_TEXTAREA_HEIGHT);
    const minHeight = baseHeightRef.current ?? nextHeight;
    textarea.style.height = `${Math.max(minHeight, nextHeight)}px`;
    textarea.style.overflowY =
      textarea.scrollHeight > MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
  }, [value]);

  return (
    <div className="sticky bottom-0 z-20 bg-gradient-to-t from-slate-100/95 via-slate-100/70 to-transparent px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-4 dark:from-dark-900/95 dark:via-dark-900/70 sm:px-6 sm:pb-6 sm:pt-8">
      <div className="mx-auto w-full max-w-3xl">
        <div className="rounded-3xl border border-slate-300 bg-white/90 p-3 shadow-[0_20px_45px_rgba(15,23,42,0.08)] backdrop-blur dark:border-white/10 dark:bg-dark-800/85 dark:shadow-[0_20px_60px_rgba(0,0,0,0.45)] sm:p-4">
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
            disabled={disabled}
            className="w-full resize-none bg-transparent text-base text-slate-700 placeholder:text-slate-400 focus:outline-none dark:text-slate-200 dark:placeholder:text-slate-500"
          />

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <div className="group relative">
                <button
                  type="button"
                  aria-label="Attach file"
                  disabled
                  className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-slate-300 bg-white text-slate-400 dark:border-white/10 dark:bg-dark-700 dark:text-slate-500"
                >
                  <Paperclip className="h-4 w-4" />
                </button>
                <span className="pointer-events-none absolute -top-10 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-[11px] text-white opacity-0 shadow-sm transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 dark:bg-slate-100 dark:text-slate-900">
                  Upload options coming soon
                </span>
              </div>

              {workspace === "learn" && (
                <DepthDropdown value={depthLevel} onChange={onDepthChange} />
              )}
            </div>

            <button
              type="button"
              aria-label="Send message"
              onClick={() => void handleSend()}
              disabled={isSendDisabled}
              className={`ml-auto inline-flex h-11 w-11 items-center justify-center rounded-2xl transition ${
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
