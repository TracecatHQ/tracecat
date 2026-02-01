"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"
import { useOrgMembership } from "@/hooks/use-org-membership"

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
  const { canAdministerOrg, isLoading: orgMembershipLoading } =
    useOrgMembership()
  const router = useRouter()

  const isLoading = userIsLoading || orgMembershipLoading

  useEffect(() => {
    if (!isLoading) {
      if (requireAuth && !user) {
        router.push(redirectTo)
      } else if (requireOrgAdmin && !canAdministerOrg) {
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

  if (requireOrgAdmin && !canAdministerOrg) {
    return null
  }

  if (requireSuperuser && !user?.isSuperuser) {
    return null
  }

  return <>{children}</>
}
