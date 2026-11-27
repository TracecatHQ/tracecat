"use client"

import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { AuthLayout } from "@/components/auth/auth-layout"
import { SignUp } from "@/components/auth/sign-up"
import { CenteredSpinner } from "@/components/loading/spinner"

function SignUpContent() {
  const searchParams = useSearchParams()
  const returnUrl = searchParams?.get("returnUrl") ?? null
  const organizationSlug = searchParams?.get("org") ?? null

  return (
    <AuthLayout organizationSlug={organizationSlug}>
      <SignUp returnUrl={returnUrl} organizationSlug={organizationSlug} />
    </AuthLayout>
  )
}

export default function Page() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <SignUpContent />
    </Suspense>
  )
}
