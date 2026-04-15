import { z } from "zod";

export const MessageRequestSchema = z
  .object({
    content: z.string().min(1),
    mode: z.enum(["learn", "chat", "summary"]).optional(),
  })
  .passthrough()
  .superRefine((value, ctx) => {
    if ("user_id" in value) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "user_id must not be supplied by the client",
      });
    }
  });

export type MessageRequest = z.infer<typeof MessageRequestSchema>;
