import { useEffect, useId, useRef, useState } from "react";
import FocusTrap from "focus-trap-react";
import {
  Check,
  Copy,
  Link as LinkIcon,
  Globe,
  MessageCircle,
} from "lucide-react";
import { createShare } from "../../api";
import type { ShareAccessLevel, ShareKind } from "../../types/shares";
import { notifyToast } from "../../lib/toast";

interface ShareModalProps {
  open: boolean;
  messageId?: string | null;
  conversationId?: string | null;
  defaultKind?: ShareKind;
  allowKindSelection?: boolean;
  onClose: () => void;
}

const ACCESS_OPTIONS: Array<{
  value: ShareAccessLevel;
  label: string;
  description: string;
  icon: JSX.Element;
}> = [
  {
    value: "public",
    label: "Public",
    description: "Accessible to anyone with the link.",
    icon: <Globe className="h-4 w-4" />,
  },
];

const SHARE_KIND_OPTIONS: Array<{
  value: ShareKind;
  label: string;
  description: string;
  icon: JSX.Element;
}> = [
  {
    value: "response",
    label: "Response only",
    description: "Share this assistant response.",
    icon: <MessageCircle className="h-4 w-4" />,
  },
  {
    value: "conversation",
    label: "Conversation snapshot",
    description: "Share a limited snapshot of the conversation.",
    icon: <Globe className="h-4 w-4" />,
  },
];

export default function ShareModal({
  open,
  messageId,
  conversationId,
  defaultKind = "response",
  allowKindSelection = false,
  onClose,
}: ShareModalProps): JSX.Element | null {
  const accessLevel: ShareAccessLevel = "public";
  const [shareKind, setShareKind] = useState<ShareKind>(defaultKind);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const modalRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  const accessDescription = ACCESS_OPTIONS[0]?.description;

  useEffect(() => {
    if (!open) return;
    setShareUrl(null);
    setError(null);
    setCopied(false);
    setIsSubmitting(false);
    setShareKind(defaultKind);
  }, [open, messageId, conversationId, defaultKind]);

  useEffect(() => {
    if (!open) return;
    closeButtonRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  if (!open) return null;

  const handleCreateShare = async () => {
    if (shareKind === "response" && (!messageId || !isUuid(messageId))) {
      setError("Share is available once the response is saved.");
      return;
    }
    if (shareKind === "conversation" && (!conversationId || !isUuid(conversationId))) {
      setError("Share is available once the conversation is saved.");
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await createShare({
        message_id: shareKind === "response" ? messageId ?? undefined : undefined,
        conversation_id:
          shareKind === "conversation" ? conversationId ?? undefined : undefined,
        share_kind: shareKind,
        access_level: accessLevel,
      });
      setShareUrl(response.share_url);
      notifyToast("Share link created.", "success");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to create share";
      setError(message);
      notifyToast("Failed to create share.", "error");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCopy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      notifyToast("Link copied.", "success");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      notifyToast("Failed to copy link.", "error");
    }
  };

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/80 backdrop-blur-md"
        onClick={onClose}
      ></div>
      <FocusTrap
        active={open}
        focusTrapOptions={{
          initialFocus: () => closeButtonRef.current ?? modalRef.current,
          fallbackFocus: () => modalRef.current ?? document.body,
          returnFocusOnDeactivate: true,
          escapeDeactivates: false,
          clickOutsideDeactivates: false,
        }}
      >
        <div
          ref={modalRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
          className="relative w-full max-w-lg rounded-2xl border border-dark-600 bg-dark-800 p-6 shadow-2xl"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 id={titleId} className="text-lg font-semibold text-white">
                Share
              </h3>
              <p className="text-sm text-gray-400">
                Create a secure link to share.
              </p>
            </div>
            <button
              ref={closeButtonRef}
              onClick={onClose}
              className="text-gray-400 hover:text-white transition-colors text-sm"
              aria-label="Close share dialog"
            >
              Close
            </button>
          </div>

        <div className="mt-5 space-y-4">
          {allowKindSelection && (
            <div className="space-y-2">
              <p className="text-xs uppercase tracking-[0.2em] text-gray-500">
                Share
              </p>
              <div className="grid gap-2">
                {SHARE_KIND_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    onClick={() => setShareKind(option.value)}
                    className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-left transition ${
                      shareKind === option.value
                        ? "border-cyan-400/50 bg-cyan-500/10"
                        : "border-white/10 bg-dark-900/40 hover:border-white/20"
                    }`}
                  >
                    <span className="mt-0.5 text-cyan-200">{option.icon}</span>
                    <span className="flex-1">
                      <span className="block text-sm font-medium text-white">
                        {option.label}
                      </span>
                      <span className="block text-xs text-gray-400">
                        {option.description}
                      </span>
                    </span>
                    {shareKind === option.value && (
                      <Check className="h-4 w-4 text-cyan-300" />
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          {shareKind === "conversation" && (
            <p className="text-xs text-gray-400">
              Note: Only a limited snapshot of the conversation is shared. Full
              conversation history cannot be viewed via this link.
            </p>
          )}

          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.2em] text-gray-500">
              Access level
            </p>
            <div className="grid gap-2">
              {ACCESS_OPTIONS.map((option) => (
                <div
                  key={option.value}
                  className="flex items-start gap-3 rounded-xl border border-cyan-400/50 bg-cyan-500/10 px-4 py-3 text-left"
                >
                  <span className="mt-0.5 text-cyan-200">{option.icon}</span>
                  <span className="flex-1">
                    <span className="block text-sm font-medium text-white">
                      {option.label}
                    </span>
                    <span className="block text-xs text-gray-400">
                      {option.description}
                    </span>
                  </span>
                  <Check className="h-4 w-4 text-cyan-300" />
                </div>
              ))}
            </div>
            {accessDescription && (
              <p className="text-[11px] text-gray-500">{accessDescription}</p>
            )}
          </div>
        </div>

        {error && <p className="mt-4 text-sm text-red-400">{error}</p>}

        {shareUrl && (
          <div className="mt-5 rounded-xl border border-white/10 bg-dark-900/60 p-4">
            <p className="text-xs uppercase tracking-[0.2em] text-gray-500">
              Share link
            </p>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="flex-1 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-xs text-gray-200 break-all">
                {shareUrl}
              </div>
              <button
                onClick={handleCopy}
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-white/10 bg-dark-700 px-3 py-2 text-xs text-white hover:bg-dark-600"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5 text-cyan-300" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        )}

          <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-end">
            <button
              onClick={onClose}
              className="rounded-xl border border-white/10 px-4 py-2 text-sm text-gray-300 hover:text-white"
            >
              Done
            </button>
            <button
              onClick={handleCreateShare}
              disabled={isSubmitting}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-500 px-4 py-2 text-sm font-semibold text-black transition hover:bg-cyan-400 disabled:opacity-60"
            >
              <LinkIcon className="h-4 w-4" />
              {isSubmitting ? "Creating..." : "Create link"}
            </button>
          </div>
        </div>
      </FocusTrap>
    </div>
  );
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}
