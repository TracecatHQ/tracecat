"use client"

import { type ComponentPropsWithoutRef, useState } from "react"
import { authSamlDatabaseLogin } from "@/client"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  sanitizeReturnUrl,
  serializeClearPostAuthReturnUrlCookie,
  serializePostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"

type SamlSSOButtonProps = ComponentPropsWithoutRef<typeof Button> & {
  returnUrl?: string | null
}

function setPostAuthReturnUrlCookie(returnUrl?: string | null): void {
  const secure = window.location.protocol === "https:"
  const sanitizedReturnUrl = sanitizeReturnUrl(returnUrl)
  document.cookie = sanitizedReturnUrl
    ? serializePostAuthReturnUrlCookie(sanitizedReturnUrl, secure)
    : serializeClearPostAuthReturnUrlCookie(secure)
}

export function SamlSSOButton({ returnUrl, ...props }: SamlSSOButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    try {
      setIsLoading(true)
      // Call api/auth/saml/login
      const { redirect_url } = await authSamlDatabaseLogin()
      setPostAuthReturnUrlCookie(returnUrl)
      window.location.href = redirect_url
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
