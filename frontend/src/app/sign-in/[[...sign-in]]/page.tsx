"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect } from "react"
import { SignIn } from "@/components/auth/sign-in"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"

function SignInContent() {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnUrl = searchParams?.get("returnUrl") ?? null

  useEffect(() => {
    if (user) {
      // Redirect to returnUrl if provided, otherwise to workspaces
      const redirectTo = returnUrl || "/workspaces"
      router.push(redirectTo)
    }
  }, [user, router, returnUrl])

  if (userIsLoading || user) {
    return <CenteredSpinner />
  }

  return (
    <div className="flex size-full items-center justify-center">
      <SignIn
        className="flex size-16 w-full justify-center"
        returnUrl={returnUrl}
      />
    </div>
  )
}

export default function Page() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <SignInContent />
    </Suspense>
  )
}
