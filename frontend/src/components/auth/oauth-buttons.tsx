"use client"

import { type ComponentPropsWithoutRef, useState } from "react"
import { authOauthGoogleDatabaseAuthorize } from "@/client"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"

type OAuthButtonProps = ComponentPropsWithoutRef<typeof Button>
export function GithubOAuthButton(props: OAuthButtonProps) {
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
export function GoogleOAuthButton(props: OAuthButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    try {
      setIsLoading(true)
      const { authorization_url } = await authOauthGoogleDatabaseAuthorize({
        scopes: ["openid", "email", "profile"],
      })
      window.location.href = authorization_url
    } catch (error) {
      console.error("Error authorizing with Google", error)
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
        <Icons.google className="mr-2 size-4" />
      )}{" "}
      Google
    </Button>
  )
}
