"use client"

import { ComponentPropsWithoutRef, useState } from "react"

import { thirdPartyAuthFlow } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Icons } from "@/components/icons"

type OAuthButtonProps = ComponentPropsWithoutRef<typeof Button>
export function GithubOAuthButton(props: OAuthButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    setIsLoading(true)
    await thirdPartyAuthFlow("github")
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
        <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
      ) : (
        <Icons.gitHub className="mr-2 h-4 w-4" />
      )}{" "}
      Github
    </Button>
  )
}
export function GoogleOAuthButton(props: OAuthButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    setIsLoading(true)
    await thirdPartyAuthFlow("google")
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
        <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
      ) : (
        <Icons.google className="mr-2 h-4 w-4" />
      )}{" "}
      Google
    </Button>
  )
}
