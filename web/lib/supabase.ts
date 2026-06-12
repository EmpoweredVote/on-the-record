import { createClient } from "@supabase/supabase-js";

// Read-only client: the site only ever selects (RLS allows public read on the
// `civic` schema; writes happen from the Python pipeline with the service key).
export function supabase() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY (see web/.env.local.example)"
    );
  }
  return createClient(url, key, { db: { schema: "civic" } });
}
