import { createClient } from "@supabase/supabase-js";

// Browser client, used only for clinician sign-in/session handling. All data
// goes through apps/api (see docs/web) -- never query tables from here.
//
// createClient throws on an empty URL, which would fail `next build` (CI, or a
// first Vercel build before env vars are set). Fall back to inert placeholders
// so the build succeeds; with them, sign-in fails at runtime rather than crashing
// the build. Set NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY for real.
export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || "http://localhost:54321",
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "missing-anon-key",
);
