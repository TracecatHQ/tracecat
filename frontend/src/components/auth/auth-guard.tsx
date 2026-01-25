"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"

interface AuthGuardProps {
  children: React.ReactNode
  requireAuth?: boolean
  requirePrivileged?: boolean
  requireSuperuser?: boolean
  redirectTo?: string
}

export function AuthGuard({
  children,
  requireAuth = true,
  requirePrivileged = false,
  requireSuperuser = false,
  redirectTo = "/",
}: AuthGuardProps) {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (!userIsLoading) {
      if (requireAuth && !user) {
        router.push(redirectTo)
      } else if (requirePrivileged && !user?.isPrivileged()) {
        router.push(redirectTo)
      } else if (requireSuperuser && !user?.isSuperuser) {
        router.push(redirectTo)
      }
    }
  }, [
    user,
    userIsLoading,
    requireAuth,
    requirePrivileged,
    requireSuperuser,
    redirectTo,
    router,
  ])

  if (userIsLoading) {
    return <CenteredSpinner />
  }

  if (requireAuth && !user) {
    return null
  }

  if (requirePrivileged && !user?.isPrivileged()) {
    return null
  }

  if (requireSuperuser && !user?.isSuperuser) {
    return null
  }

  return <>{children}</>
}
