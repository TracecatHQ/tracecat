"use client"

import { notFound, useRouter } from "next/navigation"
import { type ReactNode, useEffect } from "react"
import { requesterStyles } from "@/components/forms/form-styles"
import { FormsUnavailableState } from "@/components/forms/requester/requester-states"
import { RequesterTopBar } from "@/components/forms/requester/requester-top-bar"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"
import { useFormsAccess } from "@/hooks/use-forms-access"

/**
 * One requester page: the top bar and a scrolling body. Render it inside
 * {@link RequesterShell}.
 */
export function RequesterPage({
  title,
  children,
}: {
  /** Shown next to the logo, e.g. the workspace a form belongs to. */
  title: string
  children: ReactNode
}) {
  return (
    <>
      <RequesterTopBar title={title} />
      <main className={requesterStyles.main}>{children}</main>
    </>
  )
}

function RequesterGate({ children }: { children: ReactNode }) {
  const access = useFormsAccess()
  if (access === "hidden") {
    notFound()
  }
  if (access === "loading") {
    return <CenteredSpinner />
  }
  if (access === "locked") {
    return (
      <RequesterPage title="Forms">
        <FormsUnavailableState />
      </RequesterPage>
    )
  }
  return <>{children}</>
}

/**
 * Frame and gate for the requester pages under `/forms`.
 *
 * These pages sit outside the workspace shell, so nothing above them checks
 * the session. The order matters: the session is read first, a signed-out
 * visitor is sent to sign-in with a return URL, and only a signed-in visitor
 * mounts the gate that reads the Forms flag and entitlement. A signed-out
 * visit therefore fires no authenticated request.
 */
export function RequesterShell({ children }: { children: ReactNode }) {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (userIsLoading || user) {
      return
    }
    const returnUrl = `${window.location.pathname}${window.location.search}`
    router.replace(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
  }, [user, userIsLoading, router])

  let content: ReactNode = null
  if (userIsLoading) {
    content = <CenteredSpinner />
  } else if (user) {
    content = <RequesterGate>{children}</RequesterGate>
  }
  return <div className={requesterStyles.frame}>{content}</div>
}
