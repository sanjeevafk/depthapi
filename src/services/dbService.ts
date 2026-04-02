import { supabase } from "../lib/supabase";
import { supabaseConfigured } from "../lib/chatStoreUtils";
import type { Conversation, ChatMode, PromptMode } from "../types/chat";

export async function createConversationInDb(
  title: string,
  mode: ChatMode,
  promptMode: PromptMode,
): Promise<Conversation | null> {
  if (!supabaseConfigured) return null;

  try {
    const { data: authData } = await supabase.auth.getUser();
    if (!authData?.user) return null;

    const { data, error } = await supabase
      .from("conversations")
      .insert({
        user_id: authData.user.id,
        title,
        mode,
        settings: { mode, prompt_mode: promptMode },
      })
      .select("id, title, mode, settings, created_at, updated_at")
      .single();

    if (error) {
      console.error("Failed to create conversation in Supabase:", error);
      return null;
    }

    return data as Conversation;
  } catch (err) {
    console.error("Supabase API error when creating conversation:", err);
    return null;
  }
}
