"use client"

import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { SignUp } from "@/components/auth/sign-up"
import { CenteredSpinner } from "@/components/loading/spinner"

function SignUpContent() {
  const searchParams = useSearchParams()
  const returnUrl = searchParams?.get("returnUrl") ?? null
  const organizationSlug = searchParams?.get("org") ?? null

  return (
    <div className="flex size-full items-center justify-center">
      <SignUp
        className="flex size-16 w-full justify-center"
        returnUrl={returnUrl}
        organizationSlug={organizationSlug}
      />
    </div>
  )
}

export default function Page() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <SignUpContent />
    </Suspense>
  )
}
