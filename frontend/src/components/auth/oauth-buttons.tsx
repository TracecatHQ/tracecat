"use client"

import { ComponentPropsWithoutRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Icons } from "@/components/icons"

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
