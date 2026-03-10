"use client"

import { type ComponentPropsWithoutRef, useState } from "react"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"
import { startOidcLogin } from "@/lib/auth-login"

type OAuthButtonProps = ComponentPropsWithoutRef<typeof Button> & {
  returnUrl?: string | null
}

type OidcProviderIcon = "google" | "login"

type OidcOAuthButtonProps = OAuthButtonProps & {
  providerLabel?: string
  providerIcon?: OidcProviderIcon
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
  providerLabel = "Social login",
  providerIcon = "login",
  ...props
}: OidcOAuthButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const ProviderIcon = providerIcon === "google" ? Icons.google : Icons.login

  const handleClick = async () => {
    try {
      setIsLoading(true)
      await startOidcLogin(returnUrl)
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
