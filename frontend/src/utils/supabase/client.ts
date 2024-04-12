import { createBrowserClient } from "@supabase/ssr"

export const createClient = () =>
  createBrowserClient(
    process.env.NODE_ENV === "development"
      ? "http://localhost:8000" // In local development mode, the client (in browser) resolves localhost as the host machine
      : process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  )
