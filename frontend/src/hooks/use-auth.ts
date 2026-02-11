"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { useCallback } from "react"
import {
  type ApiError,
  type AuthAuthDatabaseLoginData,
  authAuthDatabaseLogin,
  authRegisterRegister,
} from "@/client"
import { authConfig } from "@/config/auth"
import { getCurrentUser, User } from "@/lib/auth"

async function logoutViaServerRoute(): Promise<void> {
  try {
    await fetch("/auth/logout", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
    })
  } catch (error) {
    console.warn("Failed to execute server logout route", error)
  }
}

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
      await logoutViaServerRoute()
      await queryClient.invalidateQueries({
        queryKey: ["auth"],
      })
      router.push(redirectUrl ?? "/sign-in")
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
