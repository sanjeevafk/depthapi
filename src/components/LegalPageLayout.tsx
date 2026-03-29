import { Link } from "react-router-dom";
import type { ReactNode } from "react";

interface LegalPageLayoutProps {
  title: string;
  lastUpdated: string;
  children: ReactNode;
}

export default function LegalPageLayout({
  title,
  lastUpdated,
  children,
}: LegalPageLayoutProps): JSX.Element {
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900 dark:bg-dark-900 dark:text-slate-100">
      <header className="border-b border-slate-200/70 dark:border-white/10">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-6 sm:px-6">
          <Link
            to="/"
            className="flex items-center gap-2 text-sm font-semibold tracking-tight text-slate-900 dark:text-white"
          >
            <img
              src="/favicon.svg"
              alt="KnowBear"
              className="h-7 w-7 opacity-80"
            />
            Know<span className="text-accent-teal">Bear</span>
          </Link>
          <nav aria-label="Main navigation" className="flex items-center gap-4 text-sm text-slate-600 dark:text-slate-300">
            <Link to="/" className="hover:text-slate-900 dark:hover:text-white">
              Home
            </Link>
            <Link to="/app" className="hover:text-slate-900 dark:hover:text-white">
              App
            </Link>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
        <div className="mb-10 space-y-3">
          <p className="text-sm uppercase tracking-[0.2em] text-slate-500 dark:text-slate-400">
            KnowBear
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900 dark:text-white sm:text-4xl">
            {title}
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Last updated: {lastUpdated}
          </p>
        </div>
        <div className="space-y-10 text-sm leading-relaxed text-slate-700 dark:text-slate-300 sm:text-base">
          {children}
        </div>
      </main>

      <footer className="border-t border-slate-200/70 py-6 text-center text-xs text-slate-500 dark:border-white/10 dark:text-slate-500">
        © 2026 KnowBear. All rights reserved.
      </footer>
    </div>
  );
}
