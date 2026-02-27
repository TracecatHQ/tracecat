"use client"

import { type ComponentPropsWithoutRef, useState } from "react"
import { authOauthOidcDatabaseAuthorize } from "@/client"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  sanitizeReturnUrl,
  serializeClearPostAuthReturnUrlCookie,
  serializePostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"

type OAuthButtonProps = ComponentPropsWithoutRef<typeof Button> & {
  returnUrl?: string | null
}

type OidcProviderIcon = "google" | "saml"

type OidcOAuthButtonProps = OAuthButtonProps & {
  providerLabel?: string
  providerIcon?: OidcProviderIcon
}

function setPostAuthReturnUrlCookie(returnUrl?: string | null): void {
  const secure = window.location.protocol === "https:"
  const sanitizedReturnUrl = sanitizeReturnUrl(returnUrl)
  document.cookie = sanitizedReturnUrl
    ? serializePostAuthReturnUrlCookie(sanitizedReturnUrl, secure)
    : serializeClearPostAuthReturnUrlCookie(secure)
}

export function GithubOAuthButton({
  returnUrl: _,
  ...props
}: OAuthButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    setIsLoading(true)
    // await thirdPartyAuthFlow("github")
    setIsLoading(false)
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
        <Icons.gitHub className="mr-2 size-4" />
      )}{" "}
      Github
    </Button>
  )
}

export function OidcOAuthButton({
  returnUrl,
  providerLabel = "Single sign-on",
  providerIcon = "saml",
  ...props
}: OidcOAuthButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const ProviderIcon = providerIcon === "google" ? Icons.google : Icons.saml

  const handleClick = async () => {
    try {
      setIsLoading(true)
      const { authorization_url } = await authOauthOidcDatabaseAuthorize()
      setPostAuthReturnUrlCookie(returnUrl)
      window.location.href = authorization_url
    } catch (error) {
      console.error("Error authorizing with OIDC", error)
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
        <ProviderIcon className="mr-2 size-4" />
      )}{" "}
      {providerLabel}
    </Button>
  )
}

export function GoogleOAuthButton(props: OAuthButtonProps) {
  return (
    <OidcOAuthButton {...props} providerLabel="Google" providerIcon="google" />
  )
}
