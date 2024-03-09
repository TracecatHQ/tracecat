"use client"

import { createContext, useContext, useState } from "react"
import { createClient } from "@/utils/supabase/client"
import { type Session, type SupabaseClient } from "@supabase/supabase-js"

type MaybeSession = Session | null

type SupabaseContext = {
  supabase: SupabaseClient
  session: MaybeSession
}

const Context = createContext<SupabaseContext>({} as SupabaseContext)

export default function SupabaseProvider({
  children,
  session,
}: {
  children: React.ReactNode
  session: MaybeSession
}) {
  const [supabase] = useState(() => createClient())

  return (
    <Context.Provider value={{ supabase, session }}>
      <>{children}</>
    </Context.Provider>
  )
}
export const useSupabase = () => useContext(Context)
