"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function WorkspaceCredentialsRedirectPage() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()

  useEffect(() => {
    router.replace(`/workspaces/${workspaceId}/settings/secrets`)
  }, [router, workspaceId])

  return <CenteredSpinner />
}
