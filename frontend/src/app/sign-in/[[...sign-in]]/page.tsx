"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useState } from "react"
import { SignIn } from "@/components/auth/sign-in"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"
import { getPostAuthRedirectPath } from "@/lib/auth-redirect"
import { sanitizeReturnUrl } from "@/lib/auth-return-url"
import { getMfaStatus, isMfaPending } from "@/lib/webauthn"

function SignInContent() {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnUrl = sanitizeReturnUrl(searchParams?.get("returnUrl") ?? null)
  const organizationSlug = searchParams?.get("org") ?? null

  // A signed-in session that still owes a security key check reads as signed
  // out everywhere except /auth/mfa, so ask that endpoint before showing the
  // sign-in form.
  const [mfaPending, setMfaPending] = useState<boolean | null>(null)
  useEffect(() => {
    if (userIsLoading || user) {
      return
    }
    let cancelled = false
    getMfaStatus()
      .then((status) => {
        if (!cancelled) {
          setMfaPending(!!status && isMfaPending(status.state))
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMfaPending(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [user, userIsLoading])

  useEffect(() => {
    if (!mfaPending) {
      return
    }
    const query = returnUrl ? `?returnUrl=${encodeURIComponent(returnUrl)}` : ""
    router.replace(`/mfa${query}`)
  }, [mfaPending, router, returnUrl])

  useEffect(() => {
    if (!user) {
      return
    }
    router.replace(getPostAuthRedirectPath({ returnUrl }))
  }, [user, router, returnUrl])

  if (userIsLoading || user || mfaPending !== false) {
    return <CenteredSpinner />
  }

  return (
    <div className="flex size-full items-center justify-center">
      <SignIn
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
      <SignInContent />
    </Suspense>
  )
}
