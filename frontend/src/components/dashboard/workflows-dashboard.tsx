"use client"

import { GitBranchIcon } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { WorkflowReadMinimal } from "@/client"
import { WorkflowsTagsDashboardTable } from "@/components/dashboard/dashboard-table"
import { ViewMode } from "@/components/dashboard/folder-view-toggle"
import { WorkflowBulkPushDialog } from "@/components/dashboard/workflow-bulk-push-dialog"
import { WorkflowFoldersTable } from "@/components/dashboard/workflow-folders-table"
import { WorkflowTagsSidebar } from "@/components/dashboard/workflow-tags-sidebar"
import { Button } from "@/components/ui/button"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { type DirectoryItem, useWorkflowTags } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function WorkflowsDashboard() {
  const workspaceId = useWorkspaceId()
  const [workflowView, _] = useLocalStorage("folder-view", ViewMode.Tags)
  const [bulkPushOpen, setBulkPushOpen] = useState(false)
  const [selectedWorkflowIds, setSelectedWorkflowIds] = useState<string[]>([])
  const [selectedFolderPaths, setSelectedFolderPaths] = useState<string[]>([])
  const selectionResetKey = workflowView === ViewMode.Folders ? 1 : 0

  const selectedCount = selectedWorkflowIds.length + selectedFolderPaths.length
  const sortedSelectedWorkflowIds = useMemo(
    () => [...selectedWorkflowIds].sort(),
    [selectedWorkflowIds]
  )
  const sortedSelectedFolderPaths = useMemo(
    () => [...selectedFolderPaths].sort(),
    [selectedFolderPaths]
  )

  const handleWorkflowSelectionChange = useCallback(
    (workflows: WorkflowReadMinimal[]) => {
      setSelectedWorkflowIds(
        Array.from(new Set(workflows.map((workflow) => workflow.id)))
      )
      setSelectedFolderPaths([])
    },
    []
  )

  const handleDirectorySelectionChange = useCallback(
    (items: DirectoryItem[]) => {
      setSelectedWorkflowIds(
        Array.from(
          new Set(
            items
              .filter(
                (item): item is Extract<DirectoryItem, { type: "workflow" }> =>
                  item.type === "workflow"
              )
              .map((item) => item.id)
          )
        )
      )
      setSelectedFolderPaths(
        Array.from(
          new Set(
            items
              .filter(
                (item): item is Extract<DirectoryItem, { type: "folder" }> =>
                  item.type === "folder"
              )
              .map((item) => item.path)
          )
        )
      )
    },
    []
  )

  useEffect(() => {
    setSelectedWorkflowIds([])
    setSelectedFolderPaths([])
    setBulkPushOpen(false)
  }, [workflowView])

  if (workflowView === ViewMode.Folders) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full flex-col gap-4 py-8">
          <div className="flex items-center justify-end">
            <Button
              onClick={() => setBulkPushOpen(true)}
              disabled={selectedCount === 0}
            >
              <GitBranchIcon className="mr-2 size-4" />
              Push selected to GitHub
              {selectedCount > 0 ? ` (${selectedCount})` : ""}
            </Button>
          </div>
          <WorkflowFoldersTable
            view={workflowView}
            onSelectionChange={handleDirectorySelectionChange}
            clearSelectionTrigger={selectionResetKey}
          />
        </div>
        <WorkflowBulkPushDialog
          open={bulkPushOpen}
          onOpenChange={setBulkPushOpen}
          workspaceId={workspaceId}
          selectedWorkflowIds={sortedSelectedWorkflowIds}
          selectedFolderPaths={sortedSelectedFolderPaths}
        />
      </div>
    )
  }

  return (
    <WorkflowTagsDashboard
      workspaceId={workspaceId}
      bulkPushOpen={bulkPushOpen}
      onBulkPushOpenChange={setBulkPushOpen}
      selectedWorkflowIds={sortedSelectedWorkflowIds}
      selectedFolderPaths={sortedSelectedFolderPaths}
      onSelectionChange={handleWorkflowSelectionChange}
      clearSelectionTrigger={selectionResetKey}
    />
  )
}

function WorkflowTagsDashboard({
  workspaceId,
  bulkPushOpen,
  onBulkPushOpenChange,
  selectedWorkflowIds,
  selectedFolderPaths,
  onSelectionChange,
  clearSelectionTrigger,
}: {
  workspaceId: string
  bulkPushOpen: boolean
  onBulkPushOpenChange: (open: boolean) => void
  selectedWorkflowIds: string[]
  selectedFolderPaths: string[]
  onSelectionChange: (workflows: WorkflowReadMinimal[]) => void
  clearSelectionTrigger: number
}) {
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

  const selectedCount = selectedWorkflowIds.length + selectedFolderPaths.length

  return (
    <div className="size-full overflow-auto">
      <div className="container grid h-full grid-cols-6 gap-8 py-8">
        <div className="col-span-1">
          <WorkflowTagsSidebar workspaceId={workspaceId} />
        </div>
        <div className="col-span-5 flex flex-col space-y-8">
          <div className="flex items-center justify-end">
            <Button
              onClick={() => onBulkPushOpenChange(true)}
              disabled={selectedCount === 0}
            >
              <GitBranchIcon className="mr-2 size-4" />
              Push selected to GitHub
              {selectedCount > 0 ? ` (${selectedCount})` : ""}
            </Button>
          </div>
          <WorkflowsTagsDashboardTable
            onSelectionChange={onSelectionChange}
            clearSelectionTrigger={clearSelectionTrigger}
          />
        </div>
      </div>
      <WorkflowBulkPushDialog
        open={bulkPushOpen}
        onOpenChange={onBulkPushOpenChange}
        workspaceId={workspaceId}
        selectedWorkflowIds={selectedWorkflowIds}
        selectedFolderPaths={selectedFolderPaths}
      />
    </div>
  )
}
