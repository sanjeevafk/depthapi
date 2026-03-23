import type { StateCreator } from "zustand";
import type { ChatState } from "../useChatStore";
import { CHAT_PREMIUM_MODES } from "../../lib/chatModes";
import {
  applyThemeClass,
  loadTheme,
  persistTheme,
  defaultIsPro,
  type ThemeMode,
} from "../../lib/chatStoreUtils";

const initialTheme = loadTheme();
applyThemeClass(initialTheme);

export type ChatUiSlice = Pick<
  ChatState,
  | "theme"
  | "isSidebarOpen"
  | "isPro"
  | "gatedModes"
  | "upgradeModalOpen"
  | "regenerationModalOpen"
  | "regenerationTargetId"
  | "setTheme"
  | "toggleTheme"
  | "setIsSidebarOpen"
  | "setIsPro"
  | "openUpgradeModal"
  | "closeUpgradeModal"
  | "openRegenerationModal"
  | "closeRegenerationModal"
>;

export const createChatUiSlice: StateCreator<ChatState, [], [], ChatUiSlice> = (
  set,
  get,
) => ({
  theme: initialTheme,
  isSidebarOpen: false,
  isPro: defaultIsPro,
  gatedModes: [...CHAT_PREMIUM_MODES],
  upgradeModalOpen: false,
  regenerationModalOpen: false,
  regenerationTargetId: null,

  setTheme: (theme: ThemeMode) => {
    applyThemeClass(theme);
    persistTheme(theme);
    set({ theme });
  },
  toggleTheme: () => {
    const next: ThemeMode = get().theme === "dark" ? "light" : "dark";
    get().setTheme(next);
  },
  setIsSidebarOpen: (isSidebarOpen) => set({ isSidebarOpen }),
  setIsPro: (isPro) => set({ isPro }),
  openUpgradeModal: () => set({ upgradeModalOpen: true }),
  closeUpgradeModal: () => set({ upgradeModalOpen: false }),
  openRegenerationModal: (messageId) =>
    set({ regenerationModalOpen: true, regenerationTargetId: messageId }),
  closeRegenerationModal: () =>
    set({ regenerationModalOpen: false, regenerationTargetId: null }),
});
