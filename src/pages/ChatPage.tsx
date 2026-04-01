import { useEffect, useState } from "react";
import { Menu, Share2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import MessageList from "../components/chat/MessageList";
import ThemeToggle from "../components/chat/ThemeToggle";
import WorkspaceInput from "../components/chat/WorkspaceInput";
import WorkspaceSidebar from "../components/chat/WorkspaceSidebar";
import WelcomeEmptyState from "../components/chat/WelcomeEmptyState";
import { UpgradeModal } from "../components/UpgradeModal";
import ShareModal from "../components/share/ShareModal";
import { useAuth } from "../context/AuthContext";
import { createCheckoutSession } from "../lib/payments";
import { notifyToast } from "../lib/toast";
import { getHealth } from "../api";
import { useConversations } from "../hooks/useConversations";
import { useMessages } from "../hooks/useMessages";
import { useChatStore } from "../stores/useChatStore";
import { useConversationStore } from "../stores/useConversationStore";
import { useMessageStore } from "../stores/useMessageStore";
import type { Workspace } from "../lib/chatStoreUtils";

const WORKSPACE_LABELS: Record<Workspace, string> = {
  learn: "Learn",
  socratic: "Socratic",
  technical: "Technical",
};
const SIDEBAR_COLLAPSE_KEY = "kb_sidebar_collapsed_v1";

export default function ChatPage(): JSX.Element {
  const { user, signInWithGoogle, signOut, profile } = useAuth();
  const navigate = useNavigate();
  const { conversations } = useConversations();

  const workspace = useConversationStore((state) => state.workspace);
  const depthLevel = useConversationStore((state) => state.depthLevel);
  const isSidebarOpen = useChatStore((state) => state.isSidebarOpen);
  const currentConversationId = useConversationStore(
    (state) => state.currentConversationId,
  );
  const isDraftThread = useConversationStore((state) => state.isDraftThread);
  const messageCount = useMessageStore((state) => state.messageIds.length);
  const selectConversation = useChatStore((state) => state.selectConversation);
  const setWorkspace = useChatStore((state) => state.setWorkspace);
  const setDepthLevel = useChatStore((state) => state.setDepthLevel);
  const setIsSidebarOpen = useChatStore((state) => state.setIsSidebarOpen);
  const startNewThread = useChatStore((state) => state.startNewThread);
  const deleteConversation = useChatStore((state) => state.deleteConversation);
  const sendMessage = useChatStore((state) => state.sendMessage);
  const [chatEnabled, setChatEnabled] = useState(true);
  const [healthMessage, setHealthMessage] = useState<string | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isSidebarPrefHydrated, setIsSidebarPrefHydrated] = useState(false);
  const [shareConversationOpen, setShareConversationOpen] = useState(false);

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
    if (isSidebarPrefHydrated || typeof window === "undefined") return;
    try {
      setIsSidebarCollapsed(
        window.localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === "true",
      );
    } catch {
      setIsSidebarCollapsed(false);
    } finally {
      setIsSidebarPrefHydrated(true);
    }
  }, [isSidebarPrefHydrated]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!isSidebarPrefHydrated) return;
    try {
      window.localStorage.setItem(
        SIDEBAR_COLLAPSE_KEY,
        String(isSidebarCollapsed),
      );
    } catch {
      // Ignore storage errors (e.g. private mode).
    }
  }, [isSidebarCollapsed, isSidebarPrefHydrated]);

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
            : health.provider?.status === "ok";
        if (disposed) return;
        setChatEnabled(backendChatEnabled);

        if (!backendChatEnabled) {
          const message =
            health.key_valid === false
              ? "Authentication failed for one or more providers. Please check credentials and try again."
              : "Chat is temporarily unavailable while provider configuration is being updated.";
          setHealthMessage(message);
          return;
        }

        setHealthMessage(null);
      } catch {
        if (disposed) return;
        setChatEnabled(false);
        setHealthMessage(
          "Chat is temporarily unavailable right now. Please try again in a moment.",
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
          <p className="mb-3 text-3xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
            Welcome back
          </p>
          <p className="mb-6 text-sm text-slate-500 dark:text-slate-400">
            Sign in to keep your conversations saved and in sync.
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
  const showPinnedPrompts = isDraftThread || messageCount === 0;
  const userName =
    user.user_metadata?.full_name || user.email?.split("@")[0] || "User";
  const avatarUrl =
    (user.user_metadata?.avatar_url as string | undefined) ?? null;

  return (
    <div className="h-[100dvh] min-h-[100dvh] overflow-hidden bg-slate-100 text-slate-900 dark:bg-dark-900 dark:text-slate-100">
      <div className="flex h-full">
        <WorkspaceSidebar
          workspace={workspace}
          conversations={conversations}
          currentConversationId={currentConversationId}
          isOpen={isSidebarOpen}
          isCollapsed={isSidebarCollapsed}
          userName={userName}
          avatarUrl={avatarUrl}
          isAuthenticated={Boolean(user)}
          onClose={() => setIsSidebarOpen(false)}
          onToggleCollapse={() => setIsSidebarCollapsed((prev) => !prev)}
          onNewThread={startNewThread}
          onGoHome={() => navigate("/?stay=1")}
          onWorkspaceChange={setWorkspace}
          onSelectConversation={(id) => void selectConversation(id)}
          onDeleteConversation={(id) => void handleDeleteConversation(id)}
          onSignIn={() => void signInWithGoogle()}
          onSignOut={() => void signOut()}
          isPro={profile?.is_pro}
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
                className="inline-flex h-11 w-11 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-600 transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50 md:hidden dark:border-white/10 dark:bg-dark-800 dark:text-slate-300 dark:hover:bg-dark-700"
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
            <div className="flex items-center gap-2">
              {currentConversationId && !isDraftThread && !showPinnedPrompts && (
                <button
                  type="button"
                  onClick={() => setShareConversationOpen(true)}
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-white/10 dark:bg-dark-800 dark:text-slate-300 dark:hover:bg-dark-700"
                >
                  <Share2 className="h-4 w-4" />
                  Share conversation
                </button>
              )}
              <ThemeToggle />
            </div>
          </header>

          <main className="flex min-h-0 flex-1 flex-col">
            {healthMessage && (
              <div className="mx-3 mt-3 sm:mx-auto sm:mt-4 w-auto sm:w-full max-w-3xl rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100">
                {healthMessage}
              </div>
            )}
            {showPinnedPrompts ? (
              <WelcomeEmptyState
                workspace={workspace}
                userName={userName}
                disabled={!chatEnabled}
                disabledReason={
                  chatEnabled
                    ? undefined
                    : "Responses are paused while provider health recovers."
                }
                onPromptSelect={(prompt) => void handlePromptSelect(prompt)}
              />
            ) : (
              <MessageList />
            )}
          </main>

          <WorkspaceInput
            workspace={workspace}
            depthLevel={depthLevel}
            onDepthChange={setDepthLevel}
            disabled={!chatEnabled}
            disabledReason="Responses are paused while provider health recovers."
          />
        </div>
      </div>

      <UpgradeModal
        isOpen={upgradeModalOpen}
        onClose={closeUpgradeModal}
        onUpgrade={handleUpgrade}
        onUseByok={handleUseByok}
      />
      <ShareModal
        open={shareConversationOpen}
        conversationId={currentConversationId}
        defaultKind="conversation"
        allowKindSelection={false}
        onClose={() => setShareConversationOpen(false)}
      />
    </div>
  );
}
