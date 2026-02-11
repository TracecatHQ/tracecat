"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
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

/* ── AUTH ACTIONS HOOK ─────────────────────────────────────────────────── */

export function useAuthActions() {
  const queryClient = useQueryClient()
  const router = useRouter()

  const login = useCallback(
    async (data: AuthAuthDatabaseLoginData) => {
      const loginResponse = await authAuthDatabaseLogin(data)
      await queryClient.invalidateQueries({
        queryKey: ["auth"],
      })
      return loginResponse
    },
    [queryClient]
  )

  const logout = useCallback(
    async (redirectUrl?: string) => {
      const logoutResponse = await authAuthDatabaseLogout()
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
