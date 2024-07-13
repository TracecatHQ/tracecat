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
    <div className="flex size-full flex-col items-center justify-center space-y-8">
      <Image src={TracecatIcon} alt="Tracecat" className="mx-auto size-16" />
      <p className="max-w-[30vw] text-center text-sm text-muted-foreground">
        Thank you for installing Tracecat. Please note that auth is disabled in
        self-hosted. Instructions for enabling auth can be found in the docs.
      </p>
      <Button className="text-xs" onClick={handleClick} disabled={isLoading}>
        {isLoading && <Icons.spinner className="mr-2 size-4 animate-spin" />}
        Continue to workflows
      </Button>
    </div>
  )
}
