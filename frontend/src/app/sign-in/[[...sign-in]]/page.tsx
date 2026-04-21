"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect } from "react"
import { SignIn } from "@/components/auth/sign-in"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"
import { getPostAuthRedirectPath } from "@/lib/auth-redirect"
import { sanitizeReturnUrl } from "@/lib/auth-return-url"
import { useAppInfo } from "@/lib/hooks"

function SignInContent() {
  const { user, userIsLoading } = useAuth()
  const { appInfo, appInfoIsLoading } = useAppInfo()
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnUrl = sanitizeReturnUrl(searchParams?.get("returnUrl") ?? null)
  const organizationSlug = searchParams?.get("org") ?? null

  useEffect(() => {
    if (!user) {
      return
    }
    if (user.isSuperuser && appInfoIsLoading) {
      return
    }
    router.replace(
      getPostAuthRedirectPath({
        isSuperuser: user.isSuperuser,
        eeMultiTenant: appInfo?.ee_multi_tenant ?? true,
        returnUrl,
      })
    )
  }, [appInfo?.ee_multi_tenant, appInfoIsLoading, user, router, returnUrl])

  if (userIsLoading || user) {
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
