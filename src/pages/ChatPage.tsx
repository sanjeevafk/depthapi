import { BrainCircuit, HelpCircle, Menu, Sparkles } from "lucide-react";
import MessageList from "../components/chat/MessageList";
import RegenerationModal from "../components/chat/RegenerationModal";
import ThemeToggle from "../components/chat/ThemeToggle";
import WorkspaceInput from "../components/chat/WorkspaceInput";
import WorkspaceSidebar from "../components/chat/WorkspaceSidebar";
import { UpgradeModal } from "../components/UpgradeModal";
import { useAuth } from "../context/AuthContext";
import { createCheckoutSession } from "../lib/payments";
import { notifyToast } from "../lib/toast";
import { useConversations } from "../hooks/useConversations";
import { useMessages } from "../hooks/useMessages";
import { useChatStore } from "../stores/useChatStore";
import type { Workspace } from "../stores/useChatStore";

const WORKSPACE_LABELS: Record<Workspace, string> = {
  learn: "Learn",
  socratic: "Socratic",
  technical: "Technical",
};

const WORKSPACE_WELCOME: Record<
  Workspace,
  { title: string; description: string }
> = {
  learn: {
    title: "Welcome to Learn Mode",
    description: "Select a depth level and ask anything.",
  },
  socratic: {
    title: "Welcome to Socratic Mode",
    description: "Ask a question and we will reason through guided follow-ups.",
  },
  technical: {
    title: "Welcome to Technical Mode",
    description:
      "Share a system, bug, or architecture topic for a deeper breakdown.",
  },
};

function WorkspaceWelcomeCard({
  workspace,
}: {
  workspace: Workspace;
}): JSX.Element {
  const content = WORKSPACE_WELCOME[workspace];

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="w-full max-w-xl rounded-3xl border border-slate-200 bg-white/70 p-8 text-center shadow-[0_20px_45px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-dark-800/70 dark:shadow-[0_20px_60px_rgba(0,0,0,0.35)]">
        <div className="mx-auto mb-6 inline-flex h-16 w-16 items-center justify-center rounded-2xl border border-teal-200 bg-teal-50 dark:border-teal-500/30 dark:bg-teal-500/10">
          {workspace === "learn" && (
            <Sparkles className="h-7 w-7 text-teal-600 dark:text-teal-300" />
          )}
          {workspace === "socratic" && (
            <HelpCircle className="h-7 w-7 text-teal-600 dark:text-teal-300" />
          )}
          {workspace === "technical" && (
            <BrainCircuit className="h-7 w-7 text-teal-600 dark:text-teal-300" />
          )}
        </div>
        <h2 className="text-3xl font-semibold text-slate-900 dark:text-slate-100">
          {content.title}
        </h2>
        <p className="mt-3 text-lg text-slate-500 dark:text-slate-400">
          {content.description}
        </p>
      </div>
    </div>
  );
}

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
  const messageIds = useChatStore((state) => state.messageIds);
  const setWorkspace = useChatStore((state) => state.setWorkspace);
  const setDepthLevel = useChatStore((state) => state.setDepthLevel);
  const setIsSidebarOpen = useChatStore((state) => state.setIsSidebarOpen);
  const startNewThread = useChatStore((state) => state.startNewThread);
  const deleteConversation = useChatStore((state) => state.deleteConversation);

  const upgradeModalOpen = useChatStore((state) => state.upgradeModalOpen);
  const closeUpgradeModal = useChatStore((state) => state.closeUpgradeModal);

  const handleUpgrade = async () => {
    try {
      await createCheckoutSession((error) => {
        notifyToast(error.message || "Unable to start checkout. Please try again.", "error");
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

  useMessages();

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
  const hasMessages = messageIds.length > 0;
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
          userName={userName}
          avatarUrl={avatarUrl}
          onClose={() => setIsSidebarOpen(false)}
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

          <main className="min-h-0 flex-1">
            {hasMessages ? (
              <MessageList />
            ) : (
              <WorkspaceWelcomeCard workspace={workspace} />
            )}
          </main>

          <WorkspaceInput
            workspace={workspace}
            depthLevel={depthLevel}
            onDepthChange={setDepthLevel}
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
