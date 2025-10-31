"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { useEffect } from "react"
import { WorkflowsTagsDashboardTable } from "@/components/dashboard/dashboard-table"
import { ViewMode } from "@/components/dashboard/folder-view-toggle"
import { WorkflowFoldersTable } from "@/components/dashboard/workflow-folders-table"
import { WorkflowTagsSidebar } from "@/components/dashboard/workflow-tags-sidebar"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useWorkflowTags } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function WorkflowsDashboard() {
  const workspaceId = useWorkspaceId()

  const [workflowView, _] = useLocalStorage("folder-view", ViewMode.Tags)

  if (workflowView === ViewMode.Folders) {
    return (
      <div className="size-full overflow-auto">
        <div className="container h-full gap-8 py-8">
          <WorkflowFoldersTable view={workflowView} />
        </div>
      </div>
    )
  }

  return <WorkflowTagsDashboard workspaceId={workspaceId} />
}

function WorkflowTagsDashboard({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryTag = searchParams?.get("tag")
  // Only fetch tags when in Tags view
  const { tags } = useWorkflowTags(workspaceId)
  // Use useEffect to handle redirect instead of calling it during render
  useEffect(() => {
    // If we navigate to a tag that doesn't exist, redirect to the workflows page
    if (queryTag && tags && !tags.some((tag) => tag.name === queryTag)) {
      router.push(`/workspaces/${workspaceId}/workflows`)
    }
  }, [queryTag, tags, router, workspaceId])
  return (
    <div className="size-full overflow-auto">
      <div className="container grid h-full grid-cols-6 gap-8 py-8">
        <div className="col-span-1">
          <WorkflowTagsSidebar workspaceId={workspaceId} />
        </div>
        <div className="col-span-5 flex flex-col space-y-8">
          <WorkflowsTagsDashboardTable />
        </div>
      </div>
    </div>
  )
}
