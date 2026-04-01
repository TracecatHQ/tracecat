"use client"

import {
  sanitizeReturnUrl,
  serializeClearPostAuthReturnUrlCookie,
  serializePostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"

export function setPostAuthReturnUrlCookie(returnUrl?: string | null): void {
  const secure = window.location.protocol === "https:"
  const sanitizedReturnUrl = sanitizeReturnUrl(returnUrl)
  document.cookie = sanitizedReturnUrl
    ? serializePostAuthReturnUrlCookie(sanitizedReturnUrl, secure)
    : serializeClearPostAuthReturnUrlCookie(secure)
}

export async function startOidcLogin(returnUrl?: string | null): Promise<void> {
  setPostAuthReturnUrlCookie(returnUrl)
  const response = await fetch("/api/auth/oauth/authorize", {
    credentials: "include",
  })
  if (!response.ok) {
    throw new Error(`OIDC login request failed: ${response.status}`)
  }
  const { authorization_url } = (await response.json()) as {
    authorization_url: string
  }
  window.location.href = authorization_url
}
