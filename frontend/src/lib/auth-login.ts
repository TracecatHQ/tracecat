"use client"

import { authOauthOidcDatabaseAuthorize } from "@/client"
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
  const { authorization_url } = await authOauthOidcDatabaseAuthorize()
  window.location.href = authorization_url
}
