"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { useCallback } from "react"
import {
  type ApiError,
  authAuthDatabaseLogout,
  authRegisterRegister,
} from "@/client"
import { authConfig } from "@/config/auth"
import { getBaseUrl } from "@/lib/api"
import { getCurrentUser, User } from "@/lib/auth"

/* ── AUTH ACTIONS HOOK ─────────────────────────────────────────────────── */

export function useAuthActions(orgSlug?: string | null) {
  const queryClient = useQueryClient()
  const router = useRouter()

  const login = useCallback(
    async (data: { formData: { username: string; password: string } }) => {
      const params = new URLSearchParams()
      if (orgSlug) {
        params.set("org", orgSlug)
      }

      const body = new URLSearchParams()
      body.set("username", data.formData.username)
      body.set("password", data.formData.password)

      const response = await fetch(
        `${getBaseUrl()}/auth/login${params.toString() ? `?${params.toString()}` : ""}`,
        {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: body.toString(),
        }
      )
      if (!response.ok) {
        throw new Error("Login failed")
      }
      await queryClient.invalidateQueries({
        queryKey: ["auth"],
      })
      return undefined
    },
    [orgSlug, queryClient]
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
