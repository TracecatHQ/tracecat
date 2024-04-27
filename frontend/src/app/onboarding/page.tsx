"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useUser } from "@clerk/nextjs"
import ConfettiExplosion from "react-confetti-explosion"

import { completeOnboarding, newUserFlow } from "@/lib/onboarding"
import { Button } from "@/components/ui/button"
import { Icons } from "@/components/icons"

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
        <div className="flex flex-col items-center justify-center space-y-4 text-center">
          <div className="h-0">{isExploding && <ConfettiExplosion />}</div>
          <h3 className="text-2xl font-bold">🎉 Welcome to Tracecat!</h3>
          <p className="text-sm text-muted-foreground">
            We're an early stage startup so your feedback is incredibly valuable
            to the direction of the product. You can find us on our Discord
            channel or Github. We'd love for you to join our community!
          </p>
          <Button
            onClick={handleSubmit}
            disabled={isLoading}
            type="button"
            className="font-medium"
          >
            {isLoading ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Preparing your dashbaord
              </>
            ) : (
              <>Continue</>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
