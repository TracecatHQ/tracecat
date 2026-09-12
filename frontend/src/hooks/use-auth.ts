"use client"

import Cookies from "js-cookie"
import { useCallback } from "react"
import {
  type ApiError,
  type AuthAuthDatabaseLoginData,
  authAuthDatabaseLogin,
  authAuthDatabaseLogout,
  authRegisterRegister,
} from "@/client"
import { authConfig } from "@/config/auth"
import { getCurrentUser, User } from "@/lib/auth"
import {
  navigateToDocument,
  reloadCurrentDocument,
} from "@/lib/auth-navigation"
import { useQuery } from "@/lib/query"

/* ── AUTH ACTIONS HOOK ─────────────────────────────────────────────────── */

export function useAuthActions() {
  const login = useCallback(async (data: AuthAuthDatabaseLoginData) => {
    const loginResponse = await authAuthDatabaseLogin(data)
    reloadCurrentDocument()
    return loginResponse
  }, [])

  const logout = useCallback(async (redirectUrl?: string) => {
    const logoutResponse = await authAuthDatabaseLogout()
    Cookies.remove("tracecat:active-org-id")
    navigateToDocument(redirectUrl ?? "/sign-in")
    return logoutResponse
  }, [])

  return {
    login,
    logout,
    register: authRegisterRegister,
  }
}

/* ── MAIN AUTH HOOK (REPLACES CONTEXT) ─────────────────────────────────── */

export function useAuth() {
  const {
    data: user,
    isLoading: userIsLoading,
    error: userError,
  } = useQuery<User | null, ApiError>({
    queryKey: ["auth"],
    queryFn: async () => {
      const userRead = await getCurrentUser()
      return userRead ? new User(userRead) : null
    },
    retry: false,
    staleTime: authConfig.staleTime,
    refetchOnWindowFocus: true,
  })

  return { user: user ?? null, userIsLoading, userError }
}
