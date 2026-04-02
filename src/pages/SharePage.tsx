import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { Lock, RefreshCcw } from "lucide-react";

import { fetchShareByToken } from "../api";
import type { ShareSnapshot } from "../types/shares";
import type { ApiError } from "../lib/httpErrors";
import Mermaid from "../components/Mermaid";
import SafeImage from "../components/SafeImage";

const markdownComponents: Components = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  code({ className, children, ...props }: any) {
    const match = /language-(\w+)/.exec(className || "");
    const codeStr = String(children).replace(/\n$/, "");

    if (match && match[1] === "mermaid") {
      return <Mermaid chart={codeStr} />;
    }

    return (
      <code
        className={`${className} bg-black/40 rounded px-1.5 py-0.5 text-xs font-mono`}
        {...props}
      >
        {children}
      </code>
    );
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  pre({ children }: any) {
    return (
      <pre className="bg-black/40 p-4 rounded-xl border border-white/10 overflow-x-auto my-3">
        {children}
      </pre>
    );
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  img({ src, alt }: any) {
    if (!src) return null;
    return <SafeImage src={src} alt={alt || "Image"} />;
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  a({ ...props }: any) {
    return (
      <a
        {...props}
        target="_blank"
        rel="noopener noreferrer"
        className="underline decoration-cyan-500/40 underline-offset-4 hover:decoration-cyan-300"
      />
    );
  },
};

type LoadState = "loading" | "ready" | "password" | "expired" | "notfound" | "error";

export default function SharePage(): JSX.Element {
  const { token } = useParams();
  const [share, setShare] = useState<ShareSnapshot | null>(null);
  const [status, setStatus] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadShare = useCallback(
    async (passwordOverride?: string) => {
      if (!token) {
        setStatus("notfound");
        return;
      }
      setSubmitting(true);
      setError(null);
      try {
        const data = await fetchShareByToken(token, {
          password: passwordOverride,
        });
        setShare(data);
        setStatus("ready");
      } catch (err) {
        const apiError = err as ApiError;
        if (apiError?.status === 410) {
          setStatus("expired");
        } else if (apiError?.status === 404) {
          setStatus("notfound");
        } else if (apiError?.status === 401 || apiError?.status === 403) {
          setStatus("password");
          setError(apiError.message || "Password required.");
        } else {
          setStatus("error");
          setError(apiError?.message || "Unable to load share.");
        }
      } finally {
        setSubmitting(false);
      }
    },
    [token],
  );

  useEffect(() => {
    setStatus("loading");
    void loadShare();
  }, [loadShare]);

  const handlePasswordSubmit = async () => {
    if (!password.trim()) {
      setError("Enter the password to continue.");
      return;
    }
    await loadShare(password.trim());
  };

  if (status === "loading") {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        Loading share...
      </div>
    );
  }

  if (status === "expired") {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center space-y-2">
          <h1 className="text-xl font-semibold">Share expired</h1>
          <p className="text-sm text-gray-400">
            This share link is no longer available.
          </p>
        </div>
      </div>
    );
  }

  if (status === "notfound") {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center space-y-2">
          <h1 className="text-xl font-semibold">Share not found</h1>
          <p className="text-sm text-gray-400">
            Check the link and try again.
          </p>
        </div>
      </div>
    );
  }

  if (status === "password") {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center px-4">
        <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-dark-800 p-6 shadow-xl">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-full bg-white/5 flex items-center justify-center">
              <Lock className="h-5 w-5 text-cyan-300" />
            </div>
            <div>
              <h1 className="text-lg font-semibold">Password required</h1>
              <p className="text-xs text-gray-400">
                Enter the password to view this response.
              </p>
            </div>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void handlePasswordSubmit();
            }}
            className="mt-4 space-y-3"
          >
            <label htmlFor="share-password" className="sr-only">
              Password
            </label>
            <input
              id="share-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              className="w-full rounded-xl border border-white/10 bg-dark-900/70 px-4 py-2 text-sm text-white focus:border-cyan-400/60 focus:outline-none"
            />
            {error && <p className="text-xs text-red-400">{error}</p>}
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-xl bg-cyan-500 px-4 py-2 text-sm font-semibold text-black hover:bg-cyan-400 disabled:opacity-60"
            >
              {submitting ? "Checking..." : "Unlock"}
            </button>
          </form>
        </div>
      </div>
    );
  }

  if (status === "error" || !share) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center space-y-2">
          <h1 className="text-xl font-semibold">Unable to load share</h1>
          <p className="text-sm text-gray-400">{error || "Try again later."}</p>
          <button
            onClick={() => void loadShare()}
            className="inline-flex items-center gap-2 rounded-xl border border-white/10 px-4 py-2 text-xs text-gray-300 hover:text-white"
          >
            <RefreshCcw className="h-4 w-4" />
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
        <div className="space-y-2">
          <p className="text-xs uppercase tracking-[0.3em] text-gray-500">KnowBear</p>
          <h1 className="text-2xl font-semibold">
            {share.title || "Shared response"}
          </h1>
          <p className="text-sm text-gray-400">
            Shared on {new Date(share.created_at).toLocaleString()}.
          </p>
        </div>

        {share.share_kind === "conversation" ? (
          <div className="mt-8 space-y-4">
            <p className="text-xs text-gray-400">
              This is a limited conversation snapshot. Full history is not
              available.
            </p>
            {(share.snapshot_messages ?? []).map((message, index) => (
              <div
                key={message.id ?? `${message.role}-${index}`}
                className={`rounded-2xl border px-4 py-3 text-sm leading-relaxed ${
                  message.role === "user"
                    ? "border-slate-200 bg-white text-slate-900 dark:border-white/10 dark:bg-dark-800 dark:text-white"
                    : "border-white/10 bg-dark-800 text-gray-100"
                }`}
              >
                <div className="mb-2 text-[10px] uppercase tracking-[0.2em] text-gray-500">
                  {message.role === "user" ? "User" : "Assistant"}
                </div>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {message.content || ""}
                </ReactMarkdown>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-8 space-y-6">
            <section className="rounded-2xl border border-white/10 bg-dark-800 p-5">
              <p className="text-xs uppercase tracking-[0.2em] text-gray-400">Prompt</p>
              <p className="mt-3 text-sm text-gray-200 whitespace-pre-wrap">
                {share.prompt_text || "(No prompt captured)"}
              </p>
            </section>

            <section className="rounded-2xl border border-white/10 bg-dark-800 p-5">
              <p className="text-xs uppercase tracking-[0.2em] text-gray-400">Response</p>
              <div className="mt-4 text-sm text-gray-100 leading-relaxed">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {share.response_text}
                </ReactMarkdown>
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
