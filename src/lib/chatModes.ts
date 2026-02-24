import chatModes from '../../shared/chat_modes.json'
import {
    CHAT_MODES,
    CHAT_FREE_MODES,
    CHAT_PREMIUM_MODES,
    CHAT_PROMPT_MODES,
    LEGACY_CHAT_MODES,
    type ChatMode,
    type ConversationMode,
    type PromptMode,
} from '../types/chat'

export { CHAT_MODES, CHAT_FREE_MODES, CHAT_PREMIUM_MODES, CHAT_PROMPT_MODES, LEGACY_CHAT_MODES }

const matchesSharedModes = (label: string, a: readonly string[], b: readonly string[]) => {
    if (a.length !== b.length || !a.every(item => b.includes(item))) {
        console.warn(`Shared chat_modes.json mismatch for ${label}.`)
    }
}

if (import.meta.env.DEV) {
    matchesSharedModes('chat_modes', CHAT_MODES, chatModes.chat_modes)
    matchesSharedModes('free_modes', CHAT_FREE_MODES, chatModes.free_modes)
    matchesSharedModes('pro_modes', CHAT_PREMIUM_MODES, chatModes.pro_modes)
    matchesSharedModes('prompt_modes', CHAT_PROMPT_MODES, chatModes.prompt_modes)
    matchesSharedModes('legacy_modes', LEGACY_CHAT_MODES, chatModes.legacy_modes)
}

export const CHAT_DEFAULT_MODE: PromptMode = 'eli5'

export const CHAT_MODE_LABELS: Record<ChatMode, string> = {
    eli5: 'ELI5',
    eli10: 'ELI10',
    eli12: 'ELI12',
    eli15: 'ELI15',
    'meme-style': 'Meme Style',
    classic60: 'Classic 60',
    gentle70: 'Gentle 70',
    warm80: 'Warm 80',
    ensemble: 'Ensemble',
    'technical-depth': 'Technical Depth',
    socratic: 'Socratic',
}

export const CHAT_MODE_DESCRIPTIONS: Record<ChatMode, string> = {
    eli5: 'Kindergarten-simple answers.',
    eli10: 'Clear, kid-friendly detail.',
    eli12: 'Middle-school friendly depth.',
    eli15: 'High-school level clarity.',
    'meme-style': 'Punchy, funny analogies.',
    classic60: 'Classic, newspaper-era tone.',
    gentle70: 'Soft, patient explanations.',
    warm80: 'Warmest, simplest phrasing.',
    ensemble: 'Multi-model synthesis.',
    'technical-depth': 'Deep technical research.',
    socratic: 'Guided question flow.',
}

export const CHAT_MODE_ACCENTS: Record<ChatMode, string> = {
    eli5: 'text-emerald-200',
    eli10: 'text-cyan-200',
    eli12: 'text-blue-200',
    eli15: 'text-indigo-200',
    'meme-style': 'text-pink-200',
    classic60: 'text-amber-200',
    gentle70: 'text-orange-200',
    warm80: 'text-rose-200',
    ensemble: 'text-cyan-200',
    'technical-depth': 'text-sky-200',
    socratic: 'text-violet-200',
}

export const CHAT_MODE_STYLES: Record<ConversationMode, string> = {
    eli5: 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20',
    eli10: 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/20',
    eli12: 'bg-blue-500/10 text-blue-300 border border-blue-500/20',
    eli15: 'bg-indigo-500/10 text-indigo-300 border border-indigo-500/20',
    'meme-style': 'bg-pink-500/10 text-pink-300 border border-pink-500/20',
    classic60: 'bg-amber-500/10 text-amber-300 border border-amber-500/20',
    gentle70: 'bg-orange-500/10 text-orange-300 border border-orange-500/20',
    warm80: 'bg-rose-500/10 text-rose-300 border border-rose-500/20',
    ensemble: 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/20',
    'technical-depth': 'bg-sky-500/10 text-sky-300 border border-sky-500/20',
    socratic: 'bg-violet-500/10 text-violet-300 border border-violet-500/20',
    technical: 'bg-blue-500/10 text-blue-300 border border-blue-500/20',
    meme: 'bg-pink-500/10 text-pink-300 border border-pink-500/20',
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
    if (value === 'technical') return 'technical-depth'
    if (value === 'meme') return 'meme-style'
    return CHAT_DEFAULT_MODE
}

export const resolvePromptMode = (value?: string | null): PromptMode => {
    if (isChatMode(value) && isPromptMode(value as ChatMode)) return value as PromptMode
    if (value === 'meme') return 'meme-style'
    return CHAT_DEFAULT_MODE
}

export const isPromptMode = (mode: ChatMode): mode is PromptMode => {
    return CHAT_PROMPT_MODES.includes(mode as PromptMode)
}

export const isModeGated = (mode: ChatMode, isPro: boolean, gatedModes: ChatMode[]) => {
    return !isPro && gatedModes.includes(mode)
}

export const formatModeLabel = (mode?: ConversationMode | null) => {
    if (!mode) return CHAT_MODE_LABELS[CHAT_DEFAULT_MODE]
    if (isChatMode(mode)) return CHAT_MODE_LABELS[mode]
    if (mode === 'meme') return CHAT_MODE_LABELS['meme-style']
    if (mode === 'technical') return 'Technical'
    return typeof mode === 'string' && mode ? (mode as string).toUpperCase() : CHAT_MODE_LABELS[CHAT_DEFAULT_MODE]
}

export const toQueryLevel = (mode: ChatMode | PromptMode) => {
    if (mode === 'meme-style') return 'meme'
    if (mode === 'ensemble' || mode === 'technical-depth' || mode === 'socratic') return 'eli15'
    return mode
}

export const CHAT_DROPDOWN_MODES: ChatMode[] = [
    'eli5',
    'eli10',
    'eli12',
    'eli15',
    'classic60',
    'gentle70',
    'warm80',
]

export const CHAT_QUICK_MODES: ChatMode[] = [
    'meme-style',
    'ensemble',
    'technical-depth',
    'socratic',
]
