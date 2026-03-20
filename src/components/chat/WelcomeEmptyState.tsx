import type { Workspace } from "../../stores/useChatStore";
import {
  WORKSPACE_CONTENT,
  WORKSPACE_ICONS,
  WORKSPACE_PROMPTS,
} from "./welcomeEmptyStateConstants";

interface WelcomeEmptyStateProps {
  workspace: Workspace;
  userName?: string;
  onPromptSelect: (prompt: string) => void;
  disabled?: boolean;
  disabledReason?: string;
}

export default function WelcomeEmptyState({
  workspace,
  userName,
  onPromptSelect,
  disabled = false,
  disabledReason,
}: WelcomeEmptyStateProps): JSX.Element {
  const content = WORKSPACE_CONTENT[workspace];
  const Icon = WORKSPACE_ICONS[workspace];
  const prompts = WORKSPACE_PROMPTS[workspace];

  return (
    <section
      className="flex min-h-0 flex-1 items-center justify-center px-6 py-10"
      aria-labelledby="welcome-title"
    >
      <div className="w-full max-w-4xl">
        <div className="rounded-3xl border border-slate-200 bg-white/80 p-8 text-center shadow-[0_20px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-dark-800/70 dark:shadow-[0_20px_60px_rgba(0,0,0,0.35)]">
          <div className="mx-auto mb-6 inline-flex h-16 w-16 items-center justify-center rounded-2xl border border-teal-200 bg-teal-50 dark:border-teal-500/30 dark:bg-teal-500/10">
            <Icon className="h-7 w-7 text-teal-600 dark:text-teal-300" />
          </div>
          <h2
            id="welcome-title"
            className="text-3xl font-semibold text-slate-900 dark:text-slate-100"
          >
            {content.title}
          </h2>
          <p className="mt-3 text-lg text-slate-500 dark:text-slate-400">
            {content.description}
          </p>
          {userName && (
            <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">
              Welcome back, {userName}.
            </p>
          )}
        </div>

        <div className="mt-8" aria-label="Suggested prompts">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-500 dark:text-slate-400">
              Start with a prompt
            </h3>
            {disabled && disabledReason && (
              <span className="text-xs text-amber-600 dark:text-amber-300">
                {disabledReason}
              </span>
            )}
          </div>
          <ul className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2" role="list">
            {prompts.map((prompt) => (
              <li key={prompt.id}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onPromptSelect(prompt.prompt)}
                  aria-label={`Send prompt: ${prompt.title}`}
                  className="group flex h-full w-full flex-col rounded-2xl border border-slate-200 bg-white/80 p-4 text-left transition hover:-translate-y-0.5 hover:border-teal-300 hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/60 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-dark-800/60 dark:hover:border-teal-400/50 dark:focus-visible:ring-offset-dark-900"
                >
                  <span className="text-sm font-semibold text-slate-900 group-hover:text-teal-600 dark:text-slate-100 dark:group-hover:text-teal-300">
                    {prompt.title}
                  </span>
                  <span className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                    {prompt.description}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-5 text-xs text-slate-500 dark:text-slate-500">
            Tip: You can also type your own prompt in the composer below.
          </p>
        </div>
      </div>
    </section>
  );
}
