"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"
import { getPostAuthRedirectPath } from "@/lib/auth-redirect"

export default function HomePage() {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (userIsLoading) {
      return
    }
    router.replace(user ? getPostAuthRedirectPath({}) : "/sign-in")
  }, [user, router, userIsLoading])

  return <CenteredSpinner />
}
