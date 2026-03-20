import { useEffect, useState } from "react";
import { Menu } from "lucide-react";
import MessageList from "../components/chat/MessageList";
import RegenerationModal from "../components/chat/RegenerationModal";
import ThemeToggle from "../components/chat/ThemeToggle";
import WorkspaceInput from "../components/chat/WorkspaceInput";
import WorkspaceSidebar from "../components/chat/WorkspaceSidebar";
import WelcomeEmptyState from "../components/chat/WelcomeEmptyState";
import { UpgradeModal } from "../components/UpgradeModal";
import { useAuth } from "../context/AuthContext";
import { createCheckoutSession } from "../lib/payments";
import { notifyToast } from "../lib/toast";
import { getHealth } from "../api";
import { useConversations } from "../hooks/useConversations";
import { useMessages } from "../hooks/useMessages";
import { useChatStore } from "../stores/useChatStore";
import type { Workspace } from "../stores/useChatStore";

const WORKSPACE_LABELS: Record<Workspace, string> = {
  learn: "Learn",
  socratic: "Socratic",
  technical: "Technical",
};
const SIDEBAR_COLLAPSE_KEY = "kb_sidebar_collapsed_v1";

export default function ChatPage(): JSX.Element {
  const { user, signInWithGoogle } = useAuth();
  const { conversations } = useConversations();

  const workspace = useChatStore((state) => state.workspace);
  const depthLevel = useChatStore((state) => state.depthLevel);
  const isSidebarOpen = useChatStore((state) => state.isSidebarOpen);
  const currentConversationId = useChatStore(
    (state) => state.currentConversationId,
  );
  const selectConversation = useChatStore((state) => state.selectConversation);
  const setWorkspace = useChatStore((state) => state.setWorkspace);
  const setDepthLevel = useChatStore((state) => state.setDepthLevel);
  const setIsSidebarOpen = useChatStore((state) => state.setIsSidebarOpen);
  const startNewThread = useChatStore((state) => state.startNewThread);
  const deleteConversation = useChatStore((state) => state.deleteConversation);
  const sendMessage = useChatStore((state) => state.sendMessage);
  const [chatEnabled, setChatEnabled] = useState(true);
  const [healthMessage, setHealthMessage] = useState<string | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === "true";
    } catch {
      return false;
    }
  });

  const upgradeModalOpen = useChatStore((state) => state.upgradeModalOpen);
  const closeUpgradeModal = useChatStore((state) => state.closeUpgradeModal);

  const handleUpgrade = async () => {
    try {
      await createCheckoutSession((error) => {
        notifyToast(
          error.message || "Unable to start checkout. Please try again.",
          "error",
        );
      });
    } catch {
      notifyToast("Unable to start checkout. Please try again.", "error");
    }
  };

  const handleUseByok = () => {
    notifyToast("BYOK setup coming soon.", "info");
    closeUpgradeModal();
  };

  const handleDeleteConversation = async (conversationId: string) => {
    try {
      await deleteConversation(conversationId);
    } catch {
      notifyToast("Failed to delete conversation.", "error");
    }
  };

  const handlePromptSelect = async (prompt: string) => {
    startNewThread();
    await sendMessage(prompt);
  };

  useMessages();

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(
        SIDEBAR_COLLAPSE_KEY,
        String(isSidebarCollapsed),
      );
    } catch {
      // Ignore storage errors (e.g. private mode).
    }
  }, [isSidebarCollapsed]);

  useEffect(() => {
    if (!isSidebarOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsSidebarOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSidebarOpen, setIsSidebarOpen]);

  useEffect(() => {
    let disposed = false;

    const refreshHealth = async () => {
      try {
        const health = await getHealth();
        const backendChatEnabled =
          typeof health.chat_enabled === "boolean"
            ? health.chat_enabled
            : health.litellm?.status === "ok";
        if (disposed) return;
        setChatEnabled(backendChatEnabled);

        if (!backendChatEnabled) {
          const message =
            health.key_valid === false
              ? "Chat is temporarily unavailable because LiteLLM credentials are invalid."
              : "Chat is temporarily unavailable because LiteLLM is not configured.";
          setHealthMessage(message);
          return;
        }

        setHealthMessage(null);
      } catch {
        if (disposed) return;
        setChatEnabled(false);
        setHealthMessage(
          "Chat is temporarily unavailable while health checks are recovering.",
        );
      }
    };

    void refreshHealth();
    const timer = window.setInterval(() => {
      void refreshHealth();
    }, 15000);

    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-100 px-6 text-slate-900 dark:bg-dark-900 dark:text-white">
        <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-xl dark:border-white/10 dark:bg-dark-800">
          <h1 className="mb-3 text-2xl font-semibold">
            Sign in to start chatting
          </h1>
          <p className="mb-6 text-sm text-slate-500 dark:text-slate-400">
            Your conversations are stored securely in Supabase and synced across
            sessions.
          </p>
          <button
            onClick={() => void signInWithGoogle()}
            className="w-full rounded-xl bg-teal-600 py-3 text-sm font-semibold text-white transition hover:bg-teal-500"
          >
            Continue with Google
          </button>
        </div>
      </div>
    );
  }

  const workspaceLabel = WORKSPACE_LABELS[workspace];
  const hasConversations = conversations.length > 0;
  const userName =
    user.user_metadata?.full_name || user.email?.split("@")[0] || "User";
  const avatarUrl =
    (user.user_metadata?.avatar_url as string | undefined) ?? null;

  return (
    <div className="h-screen overflow-hidden bg-slate-100 text-slate-900 dark:bg-dark-900 dark:text-slate-100">
      <div className="flex h-full">
        <WorkspaceSidebar
          workspace={workspace}
          conversations={conversations}
          currentConversationId={currentConversationId}
          isOpen={isSidebarOpen}
          isCollapsed={isSidebarCollapsed}
          userName={userName}
          avatarUrl={avatarUrl}
          onClose={() => setIsSidebarOpen(false)}
          onToggleCollapse={() => setIsSidebarCollapsed((prev) => !prev)}
          onNewThread={startNewThread}
          onWorkspaceChange={setWorkspace}
          onSelectConversation={(id) => void selectConversation(id)}
          onDeleteConversation={(id) => void handleDeleteConversation(id)}
        />

        {isSidebarOpen && (
          <button
            type="button"
            aria-label="Close sidebar"
            className="fixed inset-0 z-30 bg-black/40 md:hidden"
            onClick={() => setIsSidebarOpen(false)}
          />
        )}

        <div className="relative z-10 flex min-w-0 flex-1 flex-col">
          <header className="flex h-16 items-center justify-between border-b border-slate-200 px-4 sm:px-6 dark:border-white/10">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setIsSidebarOpen(true)}
                aria-label="Open sidebar"
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-600 transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50 md:hidden dark:border-white/10 dark:bg-dark-800 dark:text-slate-300 dark:hover:bg-dark-700"
              >
                <Menu className="h-4 w-4" />
              </button>
              <h1 className="text-sm font-medium text-slate-500 dark:text-slate-400">
                Workspace <span className="mx-1">/</span>
                <span className="font-semibold text-slate-900 dark:text-slate-100">
                  {workspaceLabel}
                </span>
              </h1>
            </div>
            <ThemeToggle />
          </header>

          <main className="flex min-h-0 flex-1 flex-col">
            {healthMessage && (
              <div className="mx-auto mt-4 w-full max-w-3xl rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100">
                {healthMessage}
              </div>
            )}
            {hasConversations ? (
              <MessageList />
            ) : (
              <WelcomeEmptyState
                workspace={workspace}
                userName={userName}
                disabled={!chatEnabled}
                disabledReason={
                  chatEnabled
                    ? undefined
                    : "Chat is disabled while LiteLLM configuration is degraded."
                }
                onPromptSelect={(prompt) => void handlePromptSelect(prompt)}
              />
            )}
          </main>

          <WorkspaceInput
            workspace={workspace}
            depthLevel={depthLevel}
            onDepthChange={setDepthLevel}
            disabled={!chatEnabled}
            disabledReason="Chat is disabled while LiteLLM configuration is degraded."
          />
        </div>
      </div>

      <UpgradeModal
        isOpen={upgradeModalOpen}
        onClose={closeUpgradeModal}
        onUpgrade={handleUpgrade}
        onUseByok={handleUseByok}
      />
      <RegenerationModal />
    </div>
  );
}
