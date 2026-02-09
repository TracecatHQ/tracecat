"use client"

import { type ComponentPropsWithoutRef, useState } from "react"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"
import { getBaseUrl } from "@/lib/api"
import {
  sanitizeReturnUrl,
  serializeClearPostAuthReturnUrlCookie,
  serializePostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"

type SamlSSOButtonProps = ComponentPropsWithoutRef<typeof Button> & {
  returnUrl?: string | null
  orgSlug?: string | null
}

function setPostAuthReturnUrlCookie(returnUrl?: string | null): void {
  const secure = window.location.protocol === "https:"
  const sanitizedReturnUrl = sanitizeReturnUrl(returnUrl)
  document.cookie = sanitizedReturnUrl
    ? serializePostAuthReturnUrlCookie(sanitizedReturnUrl, secure)
    : serializeClearPostAuthReturnUrlCookie(secure)
}

export function SamlSSOButton({
  returnUrl,
  orgSlug,
  ...props
}: SamlSSOButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    try {
      setIsLoading(true)
      const params = new URLSearchParams()
      if (orgSlug) {
        params.set("org", orgSlug)
      }
      const response = await fetch(
        `${getBaseUrl()}/auth/saml/login${params.toString() ? `?${params.toString()}` : ""}`,
        { credentials: "include" }
      )
      if (!response.ok) {
        throw new Error("Failed to start SAML login")
      }
      const data = (await response.json()) as { redirect_url?: string }
      if (!data.redirect_url) {
        throw new Error("SAML redirect URL missing")
      }
      setPostAuthReturnUrlCookie(returnUrl)
      window.location.href = data.redirect_url
    } catch (error) {
      console.error("Error authorizing with SAML", error)
    } finally {
      setIsLoading(false)
    }
  }
  return (
    <Button
      {...props}
      variant="outline"
      onClick={handleClick}
      disabled={isLoading}
    >
      {isLoading ? (
        <Icons.spinner className="mr-2 size-4 animate-spin" />
      ) : (
        <Icons.saml className="mr-2 size-4" />
      )}{" "}
      SAML (SSO)
    </Button>
  )
}
