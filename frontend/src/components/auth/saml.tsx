"use client"

import { ComponentPropsWithoutRef, useState } from "react"
import { authSamlDatabaseAuthorize } from "@/client"

import { authConfig } from "@/config/auth"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { Icons } from "@/components/icons"

type SamlSSOButtonProps = ComponentPropsWithoutRef<typeof Button>
export function SamlSSOButton(props: SamlSSOButtonProps) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const handleClick = async () => {
    if (!authConfig.samlOrganizationExternalId) {
      console.error("SAML organization external ID is not set")
      toast({
        title: "SAML organization external ID is not set",
        description:
          "Please set the SAML organization external ID in the .env file",
      })
      return
    }
    try {
      setIsLoading(true)
      const { authorization_url } = await authSamlDatabaseAuthorize({
        organizationExternalId: authConfig.samlOrganizationExternalId,
      })
      window.location.href = authorization_url
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
