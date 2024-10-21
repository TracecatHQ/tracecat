"use client"

import { ComponentPropsWithoutRef, useState } from "react"

import { authConfig } from "@/config/auth"
import { Button } from "@/components/ui/button"
import { Icons } from "@/components/icons"

type SamlSSOButtonProps = ComponentPropsWithoutRef<typeof Button>
export function SamlSSOButton(props: SamlSSOButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    try {
      setIsLoading(true)
      window.location.href = authConfig.samlAuthorizationUrl
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
