"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { WorkflowsDashboardTable } from "@/components/dashboard/dashboard-table"
import { ViewMode } from "@/components/dashboard/folder-view-toggle"
import { WorkflowFoldersTable } from "@/components/dashboard/workflow-folders-table"
import { WorkflowTagsSidebar } from "@/components/dashboard/workflow-tags-sidebar"
import { useLocalStorage, useTags } from "@/lib/hooks"
import { useWorkspace } from "@/providers/workspace"

interface WorkflowsDashboardProps {
  workflowView?: ViewMode
  onWorkflowViewChange?: (view: ViewMode) => void
}

export function WorkflowsDashboard({
  workflowView: propWorkflowView,
  onWorkflowViewChange: propOnWorkflowViewChange,
}: WorkflowsDashboardProps) {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { tags } = useTags(workspaceId)
  const searchParams = useSearchParams()
  const queryTag = searchParams?.get("tag")

  // Use local state if props are not provided (for Next.js page components)
  const [localWorkflowView, setLocalWorkflowView] = useLocalStorage(
    "folder-view",
    ViewMode.Tags
  )
  const workflowView = propWorkflowView ?? localWorkflowView
  const onWorkflowViewChange =
    propOnWorkflowViewChange ??
    (propWorkflowView === undefined ? setLocalWorkflowView : () => {})

  // If we navigate to a tag that doesn't exist, redirect to the workflows page
  if (queryTag && !tags?.some((tag) => tag.name === queryTag)) {
    router.push(`/workspaces/${workspaceId}/workflows`)
    return null
  }

  if (workflowView === ViewMode.Folders) {
    return (
      <div className="size-full overflow-auto">
        <div className="container h-full gap-8 py-8">
          <WorkflowFoldersTable
            view={workflowView}
            setView={onWorkflowViewChange}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container grid h-full grid-cols-6 gap-8 py-8">
        <div className="col-span-1">
          <WorkflowTagsSidebar workspaceId={workspaceId} />
        </div>
        <div className="col-span-5 flex flex-col space-y-8">
          <WorkflowsDashboardTable />
        </div>
      </div>
    </div>
  )
}
