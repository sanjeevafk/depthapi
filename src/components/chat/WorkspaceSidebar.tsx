import { useEffect, useMemo, useRef } from "react";
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Code2,
  MessageCircle,
  Plus,
  Trash2,
} from "lucide-react";
import type { Conversation } from "../../types/chat";
import type { Workspace } from "../../stores/useChatStore";

interface WorkspaceSidebarProps {
  workspace: Workspace;
  conversations: Conversation[];
  currentConversationId: string | null;
  isOpen: boolean;
  isCollapsed: boolean;
  userName: string;
  avatarUrl?: string | null;
  onClose: () => void;
  onToggleCollapse: () => void;
  onNewThread: () => void;
  onWorkspaceChange: (workspace: Workspace) => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
}

interface WorkspaceOption {
  id: Workspace;
  label: string;
  description: string;
  icon: typeof BookOpen;
}

const WORKSPACE_OPTIONS: WorkspaceOption[] = [
  {
    id: "learn",
    label: "Learn",
    description: "Quick Learning",
    icon: BookOpen,
  },
  {
    id: "socratic",
    label: "Socratic",
    description: "Guided Thinking",
    icon: CircleHelp,
  },
  {
    id: "technical",
    label: "Technical",
    description: "Deep Analysis",
    icon: Code2,
  },
];

export default function WorkspaceSidebar({
  workspace,
  conversations,
  currentConversationId,
  isOpen,
  isCollapsed,
  userName,
  avatarUrl,
  onClose,
  onToggleCollapse,
  onNewThread,
  onWorkspaceChange,
  onSelectConversation,
  onDeleteConversation,
}: WorkspaceSidebarProps): JSX.Element {
  const newThreadButtonRef = useRef<HTMLButtonElement>(null);
  const labelClassName = `max-w-[160px] overflow-hidden whitespace-nowrap transition-[max-width,opacity,transform] duration-200 ${
    isCollapsed
      ? "md:max-w-0 md:opacity-0 md:translate-x-2 md:pointer-events-none"
      : "md:opacity-100 md:translate-x-0"
  }`;
  const blockFadeClassName = `overflow-hidden transition-[max-width,opacity,transform] duration-200 ${
    isCollapsed
      ? "md:max-w-0 md:opacity-0 md:translate-x-2 md:pointer-events-none"
      : "md:max-w-[220px] md:opacity-100 md:translate-x-0"
  }`;
  const sectionFadeClassName = `transition-[max-height,opacity] duration-200 ${
    isCollapsed
      ? "md:max-h-0 md:opacity-0 md:overflow-hidden md:flex-none"
      : "md:opacity-100 md:max-h-[calc(100vh-16rem)] md:flex-1"
  }`;

  useEffect(() => {
    if (!isOpen) return;
    requestAnimationFrame(() => newThreadButtonRef.current?.focus());
  }, [isOpen]);

  const recentConversations = useMemo(
    () => conversations.slice(0, 20),
    [conversations],
  );

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex-shrink-0 w-72 border-r border-slate-200 bg-slate-100/95 backdrop-blur transition-[width,transform] duration-200 md:static md:translate-x-0 dark:border-white/10 dark:bg-dark-800/95 ${
        isCollapsed ? "md:w-20" : "md:w-72"
      } ${isOpen ? "translate-x-0" : "-translate-x-full"}`}
      aria-label="Sidebar"
    >
      <button
        type="button"
        onClick={onToggleCollapse}
        aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="absolute right-0 top-1/2 hidden h-10 w-10 -translate-y-1/2 translate-x-1/2 items-center justify-center rounded-full border border-slate-300 bg-white text-slate-600 shadow-lg transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50 md:flex dark:border-white/10 dark:bg-dark-700 dark:text-slate-300 dark:hover:bg-dark-600"
      >
        {isCollapsed ? (
          <ChevronRight className="h-4 w-4" />
        ) : (
          <ChevronLeft className="h-4 w-4" />
        )}
      </button>
      <div className="flex h-full flex-col">
        <div className="border-b border-slate-200 px-4 py-5 dark:border-white/10">
          <div className="flex items-center gap-2">
            <img
              src="/favicon.svg"
              alt="KnowBear logo"
              className="h-8 w-8 drop-shadow-[0_0_8px_rgba(6,182,212,0.45)]"
            />
            <span
              className={`inline-block text-lg font-black tracking-tight leading-none text-slate-900 dark:text-slate-100 ${labelClassName}`}
            >
              Know<span className="text-cyan-500">Bear</span>
            </span>
          </div>
          <button
            ref={newThreadButtonRef}
            type="button"
            onClick={() => {
              onNewThread();
              onClose();
            }}
            className="group relative mt-5 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white text-sm font-semibold text-slate-700 transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50 dark:border-white/10 dark:bg-dark-700 dark:text-slate-200 dark:hover:bg-dark-600"
          >
            <Plus className="h-4 w-4" />
            <span className={`inline-block ${labelClassName}`}>New Thread</span>
            {isCollapsed && (
              <span className="pointer-events-none absolute left-full top-1/2 ml-3 hidden -translate-y-1/2 whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-xs text-white opacity-0 shadow-sm transition group-hover:opacity-100 md:block">
                New Thread
              </span>
            )}
          </button>
        </div>

        <div className="px-4 pt-5">
          <h2
            className={`inline-block text-xs font-semibold uppercase tracking-[0.18em] text-slate-400 dark:text-slate-500 ${labelClassName}`}
          >
            Workspaces
          </h2>
          <nav className="mt-2 space-y-1" aria-label="Workspaces">
            {WORKSPACE_OPTIONS.map((option) => {
              const isActive = workspace === option.id;
              const Icon = option.icon;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => {
                    onNewThread();
                    onWorkspaceChange(option.id);
                    onClose();
                  }}
                  className={`group relative inline-flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm transition ${
                    isActive
                      ? "bg-teal-100 text-slate-900 dark:bg-teal-500/10 dark:text-white"
                      : "text-slate-600 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-white/5"
                  } ${isCollapsed ? "md:justify-center" : ""}`}
                  aria-current={isActive ? "page" : undefined}
                >
                  <Icon className="h-4 w-4" />
                  <span
                    className={`inline-flex min-w-0 flex-col ${labelClassName}`}
                  >
                    <span className="truncate text-sm font-medium leading-tight">
                      {option.label}
                    </span>
                    <span className="truncate text-[11px] leading-tight text-slate-500 dark:text-slate-400">
                      {option.description}
                    </span>
                  </span>
                  {isCollapsed && (
                    <span className="pointer-events-none absolute left-full top-1/2 ml-3 hidden -translate-y-1/2 whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-xs text-white opacity-0 shadow-sm transition group-hover:opacity-100 md:block">
                      {option.label}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        <div
          className={`mt-5 flex-1 overflow-y-auto border-t border-slate-200 px-4 pt-4 dark:border-white/10 ${sectionFadeClassName} custom-scrollbar`}
        >
          <h2
            className={`inline-block text-xs font-semibold uppercase tracking-[0.18em] text-slate-400 dark:text-slate-500 ${labelClassName}`}
          >
            Recent Chats
          </h2>
          <div
            className="mt-3 space-y-3"
            role="list"
            aria-label="Conversations"
          >
            {recentConversations.length === 0 ? (
              <p className="rounded-xl border border-dashed border-slate-300 px-3 py-3 text-sm text-slate-500 dark:border-white/10 dark:text-slate-400">
                No conversations yet.
              </p>
            ) : (
              recentConversations.map((conversation) => {
                const isActive = conversation.id === currentConversationId;
                const conversationTitle =
                  conversation.title || "Untitled conversation";
                const openConversationAriaLabel = `Open ${
                  conversation.title || "untitled conversation"
                }`;
                const deleteConversationAriaLabel = `Delete ${
                  conversation.title || "conversation"
                }`;

                return (
                  <div
                    key={conversation.id}
                    role="listitem"
                    className={`group rounded-xl border px-3 py-2.5 transition-colors ${
                      isActive
                        ? "border-teal-300 bg-white dark:border-teal-400/30 dark:bg-dark-700"
                        : "border-slate-200 bg-white/70 hover:border-slate-300 hover:bg-white dark:border-white/10 dark:bg-dark-900/30 dark:hover:border-white/20 dark:hover:bg-dark-800/40"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          onSelectConversation(conversation.id);
                          onClose();
                        }}
                        className="min-w-0 flex-1 text-left"
                        aria-label={openConversationAriaLabel}
                      >
                        <p
                          className="truncate text-sm font-medium text-slate-800 dark:text-slate-100"
                          title={conversationTitle}
                        >
                          {conversationTitle}
                        </p>
                      </button>
                      <button
                        type="button"
                        aria-label={deleteConversationAriaLabel}
                        onClick={() => {
                          const shouldDelete = window.confirm(
                            "Delete this chat? This cannot be undone.",
                          );
                          if (!shouldDelete) return;
                          onDeleteConversation(conversation.id);
                        }}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-200 hover:text-red-500 dark:text-slate-500 dark:hover:bg-white/10 dark:hover:text-red-400"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="flex items-center gap-3 border-t border-slate-200 px-4 py-4 dark:border-white/10">
          <div className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-full bg-teal-600 text-sm font-semibold text-white">
            {avatarUrl ? (
              <img
                src={avatarUrl}
                alt="Profile"
                className="h-full w-full object-cover"
              />
            ) : (
              (userName.charAt(0) || "?").toUpperCase()
            )}
          </div>
          <div className={`min-w-0 ${blockFadeClassName}`}>
            <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
              {userName}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Workspace chat
            </p>
          </div>
          {!isCollapsed && (
            <MessageCircle
              className="ml-auto h-4 w-4 text-slate-400 dark:text-slate-500"
              aria-hidden="true"
            />
          )}
        </div>
      </div>
    </aside>
  );
}
