"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"
import { useUserScopes } from "@/lib/hooks"
import { hasGrantedScope } from "@/lib/scopes"

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
  const { userScopes, isLoading: scopesLoading } = useUserScopes(undefined, {
    enabled: requireOrgAdmin && !!user,
  })
  const router = useRouter()
  const canAdministerOrg = requireOrgAdmin
    ? hasGrantedScope("org:update", new Set(userScopes?.scopes ?? []))
    : true

  const isLoading =
    userIsLoading || (requireOrgAdmin && !!user && scopesLoading)

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
