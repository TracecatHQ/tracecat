"use client"

import { type ComponentPropsWithoutRef, useState } from "react"
import { authSamlDatabaseLogin } from "@/client"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"

type SamlSSOButtonProps = ComponentPropsWithoutRef<typeof Button>
export function SamlSSOButton(props: SamlSSOButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    try {
      setIsLoading(true)
      // Call api/auth/saml/login
      const { redirect_url } = await authSamlDatabaseLogin()
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
