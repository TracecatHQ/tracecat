"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { getWorkspaceLandingPath } from "@/lib/workspace-navigation"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function WorkspacesPage() {
  // The layout has already picked the workspace to land in: the last-viewed
  // one, else the first one the user can read. It renders this page only
  // once that is known and the user has at least one workspace.
  const workspaceId = useWorkspaceId()
  const router = useRouter()

  useEffect(() => {
    router.replace(getWorkspaceLandingPath(workspaceId))
  }, [router, workspaceId])

  // Return a loading indicator while waiting for redirection
  return <CenteredSpinner />
}
