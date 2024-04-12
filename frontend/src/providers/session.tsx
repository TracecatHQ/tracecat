"use client"

import React, {
  createContext,
  PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"
import { useRouter } from "next/navigation"
import { createClient } from "@/utils/supabase/client"
import { AuthError, Session, SupabaseClient } from "@supabase/supabase-js"

import { signOutFlow } from "@/lib/auth"

export type SessionContext =
  | {
      isLoading: true
      session: null
      error: null
      supabaseClient: SupabaseClient
      signOut: () => void
    }
  | {
      isLoading: false
      session: Session
      error: null
      supabaseClient: SupabaseClient
      signOut: () => void
    }
  | {
      isLoading: false
      session: null
      error: AuthError
      supabaseClient: SupabaseClient
      signOut: () => void
    }
  | {
      isLoading: false
      session: null
      error: null
      supabaseClient: SupabaseClient
      signOut: () => void
    }

const SessionContext = createContext<SessionContext>({
  isLoading: true,
  session: null,
  error: null,
  supabaseClient: {} as any,
  signOut: () => {},
})

export interface SessionContextProviderProps {
  initialSession?: Session | null
}

export const SessionContextProvider = ({
  initialSession = null,
  children,
}: PropsWithChildren<SessionContextProviderProps>) => {
  const [supabaseClient] = useState(() => createClient())
  const [session, setSession] = useState<Session | null>(initialSession)
  const [isLoading, setIsLoading] = useState<boolean>(!initialSession)
  const [error, setError] = useState<AuthError>()
  const router = useRouter()

  useEffect(() => {
    if (!session && initialSession) {
      setSession(initialSession)
    }
  }, [session, initialSession])

  useEffect(() => {
    let mounted = true

    async function getSession() {
      const {
        data: { session },
        error,
      } = await supabaseClient.auth.getSession()

      // only update the react state if the component is still mounted
      if (mounted) {
        if (error) {
          setError(error)
          setIsLoading(false)
          return
        }

        setSession(session)
        setIsLoading(false)
      }
      await supabaseClient.auth.startAutoRefresh()
    }

    getSession()

    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    const {
      data: { subscription },
    } = supabaseClient.auth.onAuthStateChange((event, session) => {
      if (
        session &&
        (event === "SIGNED_IN" ||
          event === "TOKEN_REFRESHED" ||
          event === "USER_UPDATED")
      ) {
        setSession(session)
      }

      if (event === "SIGNED_OUT") {
        setSession(null)
      }
    })

    return () => {
      subscription.unsubscribe()
    }
  }, [])

  const signOut = useCallback(async () => {
    // Trigger sign out flow on both the client and server
    // The below signout flow will sign out from the server and redirect to the login page
    await signOutFlow()
    router.refresh()
  }, [supabaseClient])

  const value: SessionContext = useMemo(() => {
    const constant = {
      supabaseClient,
      signOut,
    }
    if (isLoading) {
      return {
        isLoading: true,
        session: null,
        error: null,
        ...constant,
      }
    }

    if (error) {
      return {
        isLoading: false,
        session: null,
        error,
        ...constant,
      }
    }

    return {
      isLoading: false,
      session,
      error: null,
      ...constant,
    }
  }, [isLoading, session, error])

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  )
}

export const useSessionContext = () => {
  const context = useContext(SessionContext)
  if (context === undefined) {
    throw new Error(
      `useSessionContext must be used within a SessionContextProvider.`
    )
  }

  return context
}

export function useSupabaseClient<
  Database = any,
  SchemaName extends string & keyof Database = "public" extends keyof Database
    ? "public"
    : string & keyof Database,
>() {
  const context = useContext(SessionContext)
  if (context === undefined) {
    throw new Error(
      `useSupabaseClient must be used within a SessionContextProvider.`
    )
  }

  return context.supabaseClient as SupabaseClient<Database, SchemaName>
}

export const useSession = () => {
  const context = useContext(SessionContext)
  if (context === undefined) {
    throw new Error(`useSession must be used within a SessionContextProvider.`)
  }

  return context.session
}

export const useUser = () => {
  const context = useContext(SessionContext)
  if (context === undefined) {
    throw new Error(`useUser must be used within a SessionContextProvider.`)
  }

  return context.session?.user ?? null
}
