"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { useCallback } from "react"
import {
  ApiError,
  authAuthDatabaseLogout,
  authRegisterRegister,
} from "@/client"
import { authConfig } from "@/config/auth"
import { getBaseUrl } from "@/lib/api"
import { getCurrentUser, User } from "@/lib/auth"

/* ── AUTH ACTIONS HOOK ─────────────────────────────────────────────────── */

type LoginErrorBody = {
  detail?: unknown
}

async function parseLoginErrorBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? ""
  if (contentType.includes("application/json")) {
    try {
      return await response.json()
    } catch {
      return undefined
    }
  }
  const text = await response.text()
  return text.length > 0 ? text : undefined
}

function getLoginErrorDetail(body: unknown): string | undefined {
  if (typeof body === "string" && body.length > 0) {
    return body
  }
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as LoginErrorBody).detail
    if (typeof detail === "string") {
      return detail
    }
    if (detail !== undefined && detail !== null) {
      try {
        return JSON.stringify(detail)
      } catch {
        return String(detail)
      }
    }
  }
  return undefined
}

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
      const requestUrl = `${getBaseUrl()}/auth/login${params.toString() ? `?${params.toString()}` : ""}`

      const response = await fetch(requestUrl, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: body.toString(),
      })
      if (!response.ok) {
        const parsedErrorBody = await parseLoginErrorBody(response)
        const errorBody =
          parsedErrorBody === undefined
            ? { detail: response.statusText || "Login failed" }
            : parsedErrorBody
        const detail = getLoginErrorDetail(errorBody)
        const message = detail
          ? `Login failed (${response.status} ${response.statusText}): ${detail}`
          : `Login failed (${response.status} ${response.statusText})`
        throw new ApiError(
          {
            method: "POST",
            url: "/auth/login",
            mediaType: "application/x-www-form-urlencoded",
            query: orgSlug ? { org: orgSlug } : undefined,
          },
          {
            body: errorBody,
            ok: response.ok,
            status: response.status,
            statusText: response.statusText,
            url: response.url || requestUrl,
          },
          message
        )
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
