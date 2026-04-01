import { createClient } from "@supabase/supabase-js";

const supabaseUrl =
  import.meta.env.VITE_SUPABASE_URL || "https://dummy.supabase.co";
const supabasePublishableKey =
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || "dummy-key";

const looksLikeLegacyJwt = (value: string) => {
  const trimmed = value.trim();
  return trimmed.startsWith("eyJ") && trimmed.includes(".");
};

if (supabasePublishableKey === "dummy-key") {
  console.warn("Supabase publishable key missing; auth and realtime disabled.");
} else if (looksLikeLegacyJwt(supabasePublishableKey)) {
  console.warn("Supabase publishable key looks like a legacy JWT.");
}

export const supabase = createClient(supabaseUrl, supabasePublishableKey);
