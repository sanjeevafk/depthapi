import { supabase } from "../lib/supabase";
import type { ChatMode, Conversation, PromptMode } from "../types/chat";
import { supabaseConfigured } from "../lib/chatStoreUtils";

export async function createConversation(
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
    if (error) throw error;
    return (data ?? null) as Conversation | null;
  } catch (error) {
    console.error("Failed to create conversation:", error);
    return null;
  }
}

export async function updateConversationMode(
  conversationId: string,
  mode: ChatMode,
  settings: Conversation["settings"] | undefined,
): Promise<boolean> {
  if (!supabaseConfigured || conversationId.startsWith("local-")) return false;
  try {
    const { error } = await supabase
      .from("conversations")
      .update({ mode, settings })
      .eq("id", conversationId);
    if (error) throw error;
    return true;
  } catch (error) {
    console.error("Failed to update conversation mode:", {
      conversationId,
      mode,
      settings,
      error,
    });
    return false;
  }
}

export async function updateConversationSettings(
  conversationId: string,
  settings: Conversation["settings"] | undefined,
): Promise<boolean> {
  if (!supabaseConfigured || conversationId.startsWith("local-")) return false;
  try {
    const { error } = await supabase
      .from("conversations")
      .update({ settings })
      .eq("id", conversationId);
    if (error) throw error;
    return true;
  } catch (error) {
    console.error("Failed to update conversation settings:", {
      conversationId,
      settings,
      error,
    });
    return false;
  }
}

export async function updateConversationTitle(
  conversationId: string,
  title: string,
  updatedAt: string,
): Promise<boolean> {
  if (!supabaseConfigured || conversationId.startsWith("local-")) return false;
  try {
    const { error } = await supabase
      .from("conversations")
      .update({ title, updated_at: updatedAt })
      .eq("id", conversationId);
    if (error) throw error;
    return true;
  } catch (error) {
    console.error("Failed to update conversation title:", {
      conversationId,
      title,
      error,
    });
    return false;
  }
}

export async function deleteConversation(conversationId: string): Promise<boolean> {
  if (!supabaseConfigured || conversationId.startsWith("local-")) return false;
  try {
    const { error } = await supabase
      .from("conversations")
      .delete()
      .eq("id", conversationId);
    if (error) throw error;
    return true;
  } catch (error) {
    console.error("Failed to delete conversation:", error);
    return false;
  }
}
