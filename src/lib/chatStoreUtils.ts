import type { Level } from "../types";
import type { ChatMode, Conversation, Message, PromptMode } from "../types/chat";
import { CHAT_PREMIUM_MODES, isPromptMode, resolveChatMode } from "../lib/chatModes";

export type Workspace = "learn" | "socratic" | "technical";
export type ThemeMode = "dark" | "light";
export const DEPTH_LEVELS = [
  "eli5",
  "eli10",
  "eli12",
  "eli15",
  "meme",
] as const;
export type DepthLevel = (typeof DEPTH_LEVELS)[number];
export type StoreLevel = Level;
export type StoreConversation = Conversation;
export const CHAT_STORE_PREMIUM_MODES = CHAT_PREMIUM_MODES;

export const THEME_STORAGE_KEY = "kb_theme_v1";
export const DEFAULT_WORKSPACE: Workspace = "learn";
export const DEFAULT_DEPTH_LEVEL: DepthLevel = "eli12";
export const PENDING_SYNC_KEY = "kb_pending_sync_v1";

export const supabaseConfigured =
  Boolean(import.meta.env.VITE_SUPABASE_URL) &&
  Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY);

const defaultIsProEnv = import.meta.env.VITE_DEFAULT_IS_PRO;
export const defaultIsPro = defaultIsProEnv ? defaultIsProEnv === "true" : false;
export const API_URL = import.meta.env.VITE_API_URL || "";

export const createUuid = () => {
  const webCrypto: Crypto | undefined =
    typeof globalThis !== "undefined" ? globalThis.crypto : undefined;

  if (webCrypto?.randomUUID) {
    return webCrypto.randomUUID();
  }

  const getRandomValues = webCrypto?.getRandomValues
    ? webCrypto.getRandomValues.bind(webCrypto)
    : null;
  const rnd = (size: number) => {
    if (getRandomValues) {
      const arr = new Uint8Array(size);
      getRandomValues(arr);
      return arr;
    }
    return Uint8Array.from({ length: size }, () =>
      Math.floor(Math.random() * 256),
    );
  };
  const bytes = rnd(16);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex
    .slice(6, 8)
    .join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
};

export const makeLocalId = () => `local-${createUuid()}`;

export const makeClientId = () => createUuid();

export const truncateTitle = (content: string) => {
  const trimmed = content.trim().replace(/\s+/g, " ");
  if (trimmed.length <= 64) return trimmed;
  return `${trimmed.slice(0, 61)}...`;
};

export const isDepthLevel = (mode: string | null | undefined): mode is DepthLevel => {
  return DEPTH_LEVELS.includes(mode as DepthLevel);
};

export const resolveDepthLevel = (
  mode: string | null | undefined,
  fallback: DepthLevel = DEFAULT_DEPTH_LEVEL,
): DepthLevel => {
  if (isDepthLevel(mode)) return mode;
  return fallback;
};

export const resolveWorkspaceFromMode = (mode: ChatMode): Workspace => {
  if (mode === "socratic") return "socratic";
  if (mode === "technical") return "technical";
  return "learn";
};

export const resolveWorkspaceState = (
  mode: string | null | undefined,
  promptMode: string | null | undefined,
  fallbackDepth: DepthLevel,
) => {
  const resolvedMode = resolveChatMode(mode);

  if (resolvedMode === "socratic") {
    return {
      workspace: "socratic" as Workspace,
      mode: "socratic" as ChatMode,
      promptMode: resolveDepthLevel(promptMode, fallbackDepth) as PromptMode,
      depthLevel: resolveDepthLevel(promptMode, fallbackDepth),
    };
  }

  if (resolvedMode === "technical") {
    return {
      workspace: "technical" as Workspace,
      mode: "technical" as ChatMode,
      promptMode: resolveDepthLevel(promptMode, fallbackDepth) as PromptMode,
      depthLevel: resolveDepthLevel(promptMode, fallbackDepth),
    };
  }

  const nextDepth = resolveDepthLevel(
    promptMode || (isPromptMode(resolvedMode) ? resolvedMode : undefined),
    fallbackDepth,
  );

  return {
    workspace: "learn" as Workspace,
    mode: "learn" as ChatMode,
    promptMode: nextDepth as PromptMode,
    depthLevel: nextDepth,
  };
};

export const loadTheme = (): ThemeMode => {
  if (typeof window === "undefined") return "light";

  const cachedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (cachedTheme === "light" || cachedTheme === "dark") {
    return cachedTheme;
  }

  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
};

export const applyThemeClass = (theme: ThemeMode) => {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
};

export const persistTheme = (theme: ThemeMode) => {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(THEME_STORAGE_KEY, theme);
};

export const getModeForWorkspace = (workspace: Workspace): ChatMode => {
  if (workspace === "socratic") return "socratic";
  if (workspace === "technical") return "technical";
  return "learn";
};

export const asString = (value: unknown): string | undefined => {
  return typeof value === "string" ? value : undefined;
};

export const isAbortError = (error: unknown): boolean => {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: string }).name === "AbortError"
  );
};

export const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
};

export const notifyError = (message: string) => {
  console.error(message);
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("kb-toast", { detail: { type: "error", message } }),
    );
  }
};

/**
 * Returns the canonical store key for a message.
 * Preference order: clientGeneratedId > assistant_client_id > client_id > serverMessageId > id
 * This key is used as the dict key in messagesById and as entries in messageIds.
 */
export const resolveMessageKey = (message: Message): string => {
  return (
    message.clientGeneratedId ||
    message.metadata?.assistant_client_id ||
    message.metadata?.client_id ||
    message.serverMessageId ||
    message.id
  );
};

/**
 * Returns true if two message objects refer to the same logical message.
 *
 * Resolution order:
 * 1. Canonical clientId match (set at message creation, most reliable)
 * 2. Server-assigned id match (for messages loaded from the database)
 * 3. Legacy fallback for messages created before this change
 *
 * Do not add new branches. If a new identity field is introduced,
 * add it to the canonical clientId derivation in resolveMessageKey()
 * instead.
 */
export const messagesMatch = (existing: Message, incoming: Message): boolean => {
  // 1. Canonical client ID — most reliable, set at creation time
  const existingClientId =
    existing.clientGeneratedId ||
    existing.metadata?.assistant_client_id ||
    existing.metadata?.client_id;

  const incomingClientId =
    incoming.clientGeneratedId ||
    incoming.metadata?.assistant_client_id ||
    incoming.metadata?.client_id;

  if (existingClientId && incomingClientId && existingClientId === incomingClientId) {
    return true;
  }

  // 2. Server-assigned UUID — for messages loaded from Supabase that never
  //    had a client ID (e.g. messages from a previous session)
  if (existing.id && incoming.id && existing.id === incoming.id) {
    return true;
  }

  // 3. Cross-reference: server id on one side, serverMessageId on the other
  //    Handles the transition window when a local message gets its server id
  if (
    existing.serverMessageId &&
    incoming.id &&
    existing.serverMessageId === incoming.id
  ) {
    return true;
  }
  if (
    incoming.serverMessageId &&
    existing.id &&
    incoming.serverMessageId === existing.id
  ) {
    return true;
  }

  return false;
};

export const findExistingMessageKey = (
  state: { messagesById: Record<string, Message>; messageIds: string[] },
  incoming: Message,
) => {
  for (const messageKey of state.messageIds) {
    const existing = state.messagesById[messageKey];
    if (!existing) continue;
    if (messagesMatch(existing, incoming)) {
      return messageKey;
    }
  }
  return null;
};

export const buildMessageRegistry = (messages: Message[]) => {
  const messagesById: Record<string, Message> = {};
  const messageIds: string[] = [];

  for (const message of messages) {
    const key = resolveMessageKey(message);
    if (!key) {
      console.warn("Message has no identifiable key, skipping:", message);
      continue;
    }
    if (messagesById[key]) {
      messagesById[key] = { ...messagesById[key], ...message };
      continue;
    }
    messagesById[key] = message;
    messageIds.push(key);
  }

  return { messagesById, messageIds };
};
