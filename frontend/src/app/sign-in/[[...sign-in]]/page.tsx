"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "@/providers/auth"

import { SignIn } from "@/components/auth/sign-in"
import { CenteredSpinner } from "@/components/loading/spinner"

export default function Page() {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (user) {
      router.push("/workspaces")
    }
  }, [user, router])

  if (userIsLoading || user) {
    return <CenteredSpinner />
  }

  return (
    <div className="flex size-full items-center justify-center">
      <SignIn className="flex size-16 w-full justify-center" />
    </div>
  )
}
