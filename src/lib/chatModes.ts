import chatModes from '../../shared/chat_modes.json'
import {
    CHAT_MODES,
    CHAT_FREE_MODES,
    CHAT_PREMIUM_MODES,
    CHAT_PROMPT_MODES,
    LEGACY_CHAT_MODES,
    type ChatMode,
    type ConversationMode,
    type LegacyChatMode,
    type PromptMode,
} from '../types/chat'

export { CHAT_MODES, CHAT_FREE_MODES, CHAT_PREMIUM_MODES, CHAT_PROMPT_MODES, LEGACY_CHAT_MODES }

const SHARED_CONVERSATION_MODES = ['learning', 'technical', 'socratic'] as const
const SHARED_FREE_MODES = ['learning', 'socratic'] as const
const SHARED_PREMIUM_MODES = ['technical'] as const

const matchesSharedModes = (label: string, a: readonly string[], b: readonly string[]) => {
    if (a.length !== b.length || !a.every(item => b.includes(item))) {
        console.warn(`Shared chat_modes.json mismatch for ${label}.`)
    }
}

if (import.meta.env.DEV) {
    matchesSharedModes('chat_modes', SHARED_CONVERSATION_MODES, chatModes.chat_modes)
    matchesSharedModes('free_modes', SHARED_FREE_MODES, chatModes.free_modes)
    matchesSharedModes('pro_modes', SHARED_PREMIUM_MODES, chatModes.pro_modes)
    matchesSharedModes('prompt_modes', CHAT_PROMPT_MODES, chatModes.prompt_modes)
    matchesSharedModes('legacy_modes', [], chatModes.legacy_modes)
}

export const CHAT_DEFAULT_MODE: PromptMode = 'eli5'
export const CHAT_DEFAULT_CONVERSATION_MODE: ChatMode = 'learning'

export const CHAT_MODE_LABELS: Record<ChatMode, string> = {
    eli5: 'ELI5',
    eli10: 'ELI10',
    eli12: 'ELI12',
    eli15: 'ELI15',
    meme: 'Meme',
    learning: 'Learning',
    technical: 'Technical',
    socratic: 'Socratic',
}

export const CHAT_MODE_DESCRIPTIONS: Record<ChatMode, string> = {
    eli5: 'Kindergarten-simple answers.',
    eli10: 'Clear, kid-friendly detail.',
    eli12: 'Middle-school friendly depth.',
    eli15: 'High-school level clarity.',
    meme: 'Punchy, funny analogies.',
    learning: 'General learning with judged ensemble answers.',
    technical: 'Technical research with gemini-2.5-pro and deepseek-ai/DeepSeek-R1 fallback.',
    socratic: 'Guided question flow.',
}

export const CHAT_MODE_ACCENTS: Record<ChatMode, string> = {
    eli5: 'text-emerald-200',
    eli10: 'text-cyan-200',
    eli12: 'text-blue-200',
    eli15: 'text-indigo-200',
    meme: 'text-pink-200',
    learning: 'text-cyan-200',
    technical: 'text-sky-200',
    socratic: 'text-violet-200',
}

export const CHAT_MODE_STYLES: Record<ChatMode, string> = {
    eli5: 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20',
    eli10: 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/20',
    eli12: 'bg-blue-500/10 text-blue-300 border border-blue-500/20',
    eli15: 'bg-indigo-500/10 text-indigo-300 border border-indigo-500/20',
    meme: 'bg-pink-500/10 text-pink-300 border border-pink-500/20',
    learning: 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/20',
    technical: 'bg-sky-500/10 text-sky-300 border border-sky-500/20',
    socratic: 'bg-violet-500/10 text-violet-300 border border-violet-500/20',
}

export const CHAT_MODE_OPTIONS = CHAT_MODES.map(mode => ({
    id: mode,
    label: CHAT_MODE_LABELS[mode],
    description: CHAT_MODE_DESCRIPTIONS[mode],
    premium: (CHAT_PREMIUM_MODES as readonly ChatMode[]).includes(mode as ChatMode),
}))

export const isChatMode = (value?: string | null): value is ChatMode => {
    return CHAT_MODES.includes(value as ChatMode)
}

export const resolveChatMode = (value?: string | null): ChatMode => {
    if (isChatMode(value)) return value
    if (value === 'fast' || value === 'ensemble' || value === 'default' || value === 'balanced') return 'learning'
    if (value === 'technical-depth' || value === 'technical_depth') return 'technical'
    if (value === 'meme-style') return 'meme'
    return CHAT_DEFAULT_CONVERSATION_MODE
}

export const resolvePromptMode = (value?: string | null): PromptMode => {
    if (isChatMode(value) && isPromptMode(value as ChatMode)) return value as PromptMode
    if (value === 'meme-style') return 'meme'
    return CHAT_DEFAULT_MODE
}

export const isPromptMode = (mode: ChatMode): mode is PromptMode => {
    return CHAT_PROMPT_MODES.includes(mode as PromptMode)
}

export const isModeGated = (mode: ChatMode, isPro: boolean, gatedModes: ChatMode[]) => {
    return !isPro && gatedModes.includes(mode)
}

export const formatModeLabel = (mode?: ConversationMode | null) => {
    if (!mode) return CHAT_MODE_LABELS[CHAT_DEFAULT_CONVERSATION_MODE]
    if (isChatMode(mode)) return CHAT_MODE_LABELS[mode]
    if (mode === 'meme-style') return CHAT_MODE_LABELS.meme
    if (mode === 'fast' || mode === 'ensemble' || mode === 'default' || mode === 'balanced') return CHAT_MODE_LABELS.learning
    if (mode === 'technical-depth' || mode === 'technical_depth') return CHAT_MODE_LABELS.technical
    return typeof mode === 'string' && mode ? (mode as string).toUpperCase() : CHAT_MODE_LABELS[CHAT_DEFAULT_CONVERSATION_MODE]
}

export const toQueryLevel = (mode: ChatMode | PromptMode | LegacyChatMode) => {
    if (mode === 'meme-style') return 'meme'
    if (mode === 'learning' || mode === 'technical' || mode === 'socratic') return 'eli15'
    return mode
}

export const CHAT_DROPDOWN_MODES: ChatMode[] = [
    'eli5',
    'eli10',
    'eli12',
    'eli15',
]

export const CHAT_QUICK_MODES: ChatMode[] = [
    'meme',
    'learning',
    'technical',
    'socratic',
]
