import { useState } from "react";
import { Check, Copy, RefreshCcw, Share2 } from "lucide-react";
import { notifyToast } from "../../lib/toast";

interface MessageActionToolbarProps {
  content: string;
  onRegenerate?: () => void;
  onShare?: () => void;
  disabled?: boolean;
}

export default function MessageActionToolbar({
  content,
  onRegenerate,
  onShare,
  disabled,
}: MessageActionToolbarProps): JSX.Element {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (disabled) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      notifyToast("Copied to clipboard.", "success");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      notifyToast("Failed to copy message.", "error");
    }
  };

  const handleRegenerate = () => {
    if (disabled) return;
    if (onRegenerate) {
      onRegenerate();
      return;
    }
    notifyToast("Regeneration flow coming soon.", "info");
  };

  const handleShare = async () => {
    if (disabled) return;
    if (onShare) {
      onShare();
      return;
    }
    if (navigator.share) {
      try {
        await navigator.share({ text: content });
        return;
      } catch {
        // user canceled share
      }
    }
    notifyToast("Share options coming soon.", "info");
  };

  return (
    <div className="absolute right-3 top-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
      <button
        onClick={handleCopy}
        title={copied ? "Copied" : "Copy"}
        aria-label={copied ? "Copied" : "Copy"}
        disabled={disabled}
        className="h-7 w-7 rounded-lg border border-white/10 bg-dark-900/70 text-gray-300 hover:text-white flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-cyan-300" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </button>
      <button
        onClick={handleRegenerate}
        title="Regenerate"
        aria-label="Regenerate"
        disabled={disabled}
        className="h-7 w-7 rounded-lg border border-white/10 bg-dark-900/70 text-gray-300 hover:text-white flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <RefreshCcw className="h-3.5 w-3.5" />
      </button>
      <button
        onClick={handleShare}
        title="Share"
        aria-label="Share"
        disabled={disabled}
        className="h-7 w-7 rounded-lg border border-white/10 bg-dark-900/70 text-gray-300 hover:text-white flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <Share2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
