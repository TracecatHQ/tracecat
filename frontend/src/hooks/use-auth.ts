"use client"

import Cookies from "js-cookie"
import { useRouter } from "next/navigation"
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
import { type QueryClient, useQuery, useQueryClient } from "@/lib/query"
import { getMfaStatus, isMfaPending } from "@/lib/webauthn"

/* ── AUTH ACTIONS HOOK ─────────────────────────────────────────────────── */

/**
 * Drop every cached query except the current-user query so data from the
 * previous session (workspaces, org, scopes) never renders for the next user.
 * Run this after login, not logout: on logout the current page is still
 * mounted, so removed queries would refetch unauthenticated and surface 403s.
 */
function resetSessionQueries(queryClient: QueryClient): Promise<void> {
  queryClient.removeQueries({
    predicate: (query) => query.queryKey[0] !== "auth",
  })
  return queryClient.invalidateQueries({ queryKey: ["auth"] })
}

export function useAuthActions() {
  const queryClient = useQueryClient()
  const router = useRouter()

  const login = useCallback(
    async (data: AuthAuthDatabaseLoginData) => {
      const loginResponse = await authAuthDatabaseLogin(data)
      await resetSessionQueries(queryClient)
      // The new session reads as signed out until MFA is done, so send the
      // user to the challenge, keeping the sign-in query (returnUrl).
      const mfaStatus = await getMfaStatus().catch(() => null)
      if (mfaStatus && isMfaPending(mfaStatus.state)) {
        router.replace(`/mfa${window.location.search}`)
      }
      return loginResponse
    },
    [queryClient, router]
  )

  const logout = useCallback(
    async (redirectUrl?: string) => {
      const logoutResponse = await authAuthDatabaseLogout()
      Cookies.remove("tracecat:active-org-id")
      // Clear the legacy literal name as well as js-cookie's encoded name.
      document.cookie =
        "tracecat:active-org-id=; Path=/; Max-Age=0; SameSite=Lax"
      await queryClient.invalidateQueries({
        queryKey: ["auth"],
      })
      router.push(redirectUrl ?? "/sign-in")
      return logoutResponse
    },
    [queryClient, router]
  )

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
