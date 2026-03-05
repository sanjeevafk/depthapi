import { z } from 'zod'

export const ChatStreamChunkSchema = z
    .object({
        delta: z.string().optional(),
        chunk: z.string().optional(),
        assistant_message_id: z.string().optional(),
        message_id: z.string().optional(),
        error: z.string().optional(),
    })
    .passthrough()

export const LegacyStreamChunkSchema = z
    .object({
        chunk: z.string().optional(),
        warning: z.string().optional(),
        error: z.string().optional(),
    })
    .passthrough()
