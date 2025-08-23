"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { useCallback, useEffect } from "react"
import {
  type ApiError,
  type AuthAuthDatabaseLoginData,
  authAuthDatabaseLogin,
  authAuthDatabaseLogout,
  authRegisterRegister,
} from "@/client"
import { authConfig } from "@/config/auth"
import { getCurrentUser, User } from "@/lib/auth"

/* ── USER DATA HOOK ────────────────────────────────────────────────────── */

export function useUser() {
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

  const logout = useCallback(async () => {
    const logoutResponse = await authAuthDatabaseLogout()
    await queryClient.invalidateQueries({
      queryKey: ["auth"],
    })
    router.push("/sign-in")
    return logoutResponse
  }, [queryClient, router])

  return {
    login,
    logout,
    register: authRegisterRegister,
  }
}

/* ── MAIN AUTH HOOK (REPLACES CONTEXT) ─────────────────────────────────── */

export function useAuth() {
  const { user, userIsLoading, userError } = useUser()
  const router = useRouter()

  // Handle error redirect
  useEffect(() => {
    if (userError) {
      console.error("Error loading user", userError)
      router.push("/auth/error")
    }
  }, [userError, router])

  return {
    user,
    userIsLoading,
  }
}
