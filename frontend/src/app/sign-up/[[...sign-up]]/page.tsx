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
    <div className="size-full overflow-y-auto">
      <div className="flex min-h-full items-center justify-center py-8">
        <SignUp
          className="flex w-full justify-center"
          returnUrl={returnUrl}
          organizationSlug={organizationSlug}
        />
      </div>
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
