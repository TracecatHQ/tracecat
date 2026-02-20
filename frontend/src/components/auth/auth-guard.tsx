"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"

interface AuthGuardProps {
  children: React.ReactNode
  requireAuth?: boolean
  /** Require org admin privileges (platform admin OR org admin/owner) */
  requireOrgAdmin?: boolean
  requireSuperuser?: boolean
  redirectTo?: string
}

export function AuthGuard({
  children,
  requireAuth = true,
  requireOrgAdmin = false,
  requireSuperuser = false,
  redirectTo = "/",
}: AuthGuardProps) {
  const { user, userIsLoading } = useAuth()
  const canAdministerOrg = useScopeCheck("org:update")
  const router = useRouter()

  const isLoading = userIsLoading || canAdministerOrg === undefined

  useEffect(() => {
    if (!isLoading) {
      if (requireAuth && !user) {
        router.push(redirectTo)
      } else if (requireOrgAdmin && canAdministerOrg === false) {
        router.push(redirectTo)
      } else if (requireSuperuser && !user?.isSuperuser) {
        router.push(redirectTo)
      }
    }
  }, [
    user,
    isLoading,
    requireAuth,
    requireOrgAdmin,
    canAdministerOrg,
    requireSuperuser,
    redirectTo,
    router,
  ])

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (requireAuth && !user) {
    return null
  }

  if (requireOrgAdmin && canAdministerOrg === false) {
    return null
  }

  if (requireSuperuser && !user?.isSuperuser) {
    return null
  }

  return <>{children}</>
}
