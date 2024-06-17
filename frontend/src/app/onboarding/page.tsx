"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { PawPrint, WorkflowIcon } from "lucide-react"
import ConfettiExplosion from "react-confetti-explosion"

import { useUser } from "@/lib/auth"
import { completeOnboarding, newUserFlow } from "@/lib/onboarding"
import { Button } from "@/components/ui/button"
import { Icons } from "@/components/icons"

/**
 * This component only gets rendered in
 * @returns The onboarding component
 */
export default function OnboardingComponent() {
  const [isExploding, setIsExploding] = useState(false)
  const [isLoading, setIdLoading] = useState(false)
  const { user } = useUser()
  const router = useRouter()
  useEffect(() => {
    setIsExploding(true)
    return () => {
      setIsExploding(false)
    }
  }, [])

  const handleSubmit = async () => {
    setIdLoading(() => true)
    await newUserFlow()
    await completeOnboarding()
    await user?.reload()
    console.log("Onboarding complete")
    router.push("/workflows")
  }
  return (
    <div className="flex h-screen w-full items-center justify-center">
      <div className="container-sm flex aspect-auto max-w-[40vw] flex-1 items-center justify-center rounded-lg border bg-white p-16 shadow-md">
        <div className="flex flex-col items-center justify-center space-y-12 text-center">
          <div className="h-0">{isExploding && <ConfettiExplosion />}</div>
          <Button
            onClick={handleSubmit}
            disabled={isLoading}
            type="button"
            className="font-medium"
          >
            {isLoading ? (
              <>
                <Icons.spinner className="mr-2 size-4 animate-spin" />
                Preparing your dashbaord
              </>
            ) : (
              <>
                <WorkflowIcon className="mr-2 size-4" />
                Continue to workflows
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
