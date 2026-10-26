"use client"

import type { FolderDirectoryItem } from "@/client"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { useFolders } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function getFolderDeleteConfirmPhrase(folderId: string): string {
  return `delete folder ${folderId}`
}

/**
 * Confirmation for deleting a non-empty workflow folder. The folder, its
 * subfolders, and every workflow inside them are deleted. Empty folders are
 * deleted directly from the context menu without this dialog.
 */
export function FolderDeleteAlertDialog({
  open,
  onOpenChange,
  selectedFolder,
  setSelectedFolder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedFolder: FolderDirectoryItem | null
  setSelectedFolder: (selectedFolder: FolderDirectoryItem | null) => void
}) {
  const workspaceId = useWorkspaceId()
  const { deleteFolder, deleteFolderIsPending } = useFolders(workspaceId, {
    enabled: open,
  })
  const folderId = selectedFolder?.id ?? ""
  const numWorkflows = selectedFolder?.num_workflows ?? 0
  const workflowCount = `${numWorkflows} ${numWorkflows === 1 ? "workflow" : "workflows"}`

  return (
    <ConfirmDestructiveDialog
      open={open && selectedFolder !== null}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedFolder(null)
        }
        onOpenChange(isOpen)
      }}
      title="Delete folder and contents"
      description={
        <>
          <p>
            Permanently delete{" "}
            <span className="font-medium text-foreground">
              {selectedFolder?.name}
            </span>
            , its subfolders, and{" "}
            <span className="font-medium text-foreground">{workflowCount}</span>{" "}
            inside it? Their schedules are deleted too. This cannot be undone.
          </p>
          <p>To keep the workflows, move them out of the folder first.</p>
        </>
      }
      identifier={{ label: "Folder ID", value: folderId }}
      confirmPhrase={getFolderDeleteConfirmPhrase(folderId)}
      confirmPrompt={
        <>
          Type{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs font-medium">
            delete folder
          </code>{" "}
          followed by the folder ID to confirm
        </>
      }
      confirmLabel="Continue"
      finalStep={{
        title: "Are you sure?",
        description: (
          <>
            You will be deleting{" "}
            <span className="font-medium text-foreground">{workflowCount}</span>{" "}
            and every subfolder in{" "}
            <span className="font-medium text-foreground">
              {selectedFolder?.name}
            </span>
            . This cannot be undone.
          </>
        ),
        confirmLabel: numWorkflows > 0 ? `Delete ${workflowCount}` : "Delete",
      }}
      isPending={deleteFolderIsPending}
      onConfirm={async () => {
        if (!selectedFolder) return
        await deleteFolder({
          folderId: selectedFolder.id,
          recursive: true,
          deleteWorkflows: true,
        })
        setSelectedFolder(null)
        onOpenChange(false)
      }}
    />
  )
}
