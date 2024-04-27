"use client"

import { useState } from "react"
import Image from "next/image"
import { useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"

import { newUserFlow } from "@/lib/onboarding"
import { Button } from "@/components/ui/button"
import { Icons } from "@/components/icons"

/**
 *  This component is displayed when the authentication is disabled.
 * @returns AuthDisabled component
 */
export function AuthDisabled() {
  const [isLoading, setIsLoading] = useState(false)
  const router = useRouter()
  const handleClick = async () => {
    setIsLoading(true)
    await newUserFlow()
    router.push("/workflows")
  }
  return (
    <div className="flex h-full w-full flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mx-auto h-16 w-16" />
      <h1 className="text-lg font-bold">Proceed to Tracecat Cloud</h1>
      <p className="max-w-[30vw] text-center text-sm text-muted-foreground">
        Authentication is disabled. You can activate this by setting the
        TRACECAT__DISABLE_AUTH environment variable and linking your Clerk
        account.
      </p>
      <Button className="text-xs" onClick={handleClick} disabled={isLoading}>
        {isLoading && <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />}
        Continue to dashboard
      </Button>
    </div>
  )
}
