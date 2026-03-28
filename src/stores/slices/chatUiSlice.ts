import type { StateCreator } from "zustand";
import type { ChatState } from "../useChatStore";
import { CHAT_PREMIUM_MODES } from "../../lib/chatModes";
import {
  applyThemeClass,
  persistTheme,
  defaultIsPro,
  type ThemeMode,
} from "../../lib/chatStoreUtils";

export type ChatUiSlice = Pick<
  ChatState,
  | "theme"
  | "isSidebarOpen"
  | "isPro"
  | "gatedModes"
  | "upgradeModalOpen"
  | "setTheme"
  | "toggleTheme"
  | "setIsSidebarOpen"
  | "setIsPro"
  | "openUpgradeModal"
  | "closeUpgradeModal"
>;

export const createChatUiSlice: StateCreator<ChatState, [], [], ChatUiSlice> = (
  set,
  get,
) => ({
  theme: "light",
  isSidebarOpen: false,
  isPro: defaultIsPro,
  gatedModes: [...CHAT_PREMIUM_MODES],
  upgradeModalOpen: false,

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
});
