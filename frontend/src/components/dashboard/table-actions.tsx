"use client"

import {
  Copy,
  DownloadIcon,
  ExternalLink,
  FolderKanban,
  FolderUp,
  Pencil,
  TagsIcon,
  Trash2,
} from "lucide-react"
import Link from "next/link"
import type {
  FolderDirectoryItem,
  TagRead,
  WorkflowDirectoryItem,
  WorkflowReadMinimal,
} from "@/client"
import { DeleteWorkflowAlertDialogTrigger } from "@/components/dashboard/delete-workflow-dialog"
import { ActiveDialog } from "@/components/dashboard/table-common"
import {
  ContextMenuCheckboxItem,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuPortal,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
} from "@/components/ui/context-menu"
import { toast } from "@/components/ui/use-toast"
import { exportWorkflow, handleExportError } from "@/lib/export"
import { useOrgAppSettings, useWorkflowManager } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function WorkflowActions({
  item,
  setSelectedWorkflow,
  setActiveDialog,
  showMoveToFolder = true,
  availableTags,
}: {
  item: WorkflowDirectoryItem
  setSelectedWorkflow: (workflow: WorkflowReadMinimal) => void
  setActiveDialog?: (activeDialog: ActiveDialog | null) => void
  showMoveToFolder?: boolean
  availableTags?: TagRead[]
}) {
  const { appSettings } = useOrgAppSettings()
  const workspaceId = useWorkspaceId()

  const { addWorkflowTag, removeWorkflowTag } = useWorkflowManager(undefined, {
    listEnabled: false,
  })
  const enabledExport = appSettings?.app_workflow_export_enabled ?? false

  const handleExport = async (draft: boolean) => {
    if (!enabledExport) {
      return
    }

    try {
      await exportWorkflow({
        workspaceId,
        workflowId: item.id,
        format: "yaml",
        draft,
      })
    } catch (error) {
      console.error("Failed to download workflow definition as YAML:", error)
      toast(handleExportError(error as Error))
    }
  }

  return (
    <ContextMenuGroup>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        asChild
      >
        <Link
          href={`/workspaces/${workspaceId}/workflows/${item.id}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLink className="mr-2 size-3.5" />
          Open in new tab
        </Link>
      </ContextMenuItem>
      {item.alias && (
        <ContextMenuItem
          className="text-xs"
          onClick={(e) => {
            e.stopPropagation() // Prevent row click
            if (item.alias) {
              navigator.clipboard.writeText(item.alias)
            }
          }}
        >
          <Copy className="mr-2 size-3.5" />
          Copy workflow alias
        </ContextMenuItem>
      )}
      {showMoveToFolder && (
        <ContextMenuItem
          className="text-xs"
          onClick={(e) => {
            e.stopPropagation() // Prevent row click
            setSelectedWorkflow(item)
            setActiveDialog?.(ActiveDialog.WorkflowMove)
          }}
        >
          <FolderUp className="mr-2 size-3.5" />
          Move to folder
        </ContextMenuItem>
      )}
      {availableTags && availableTags.length > 0 ? (
        <ContextMenuSub>
          <ContextMenuSubTrigger
            className="text-xs"
            onClick={(e) => e.stopPropagation()}
          >
            <TagsIcon className="mr-2 size-3.5" />
            Tags
          </ContextMenuSubTrigger>
          <ContextMenuPortal>
            <ContextMenuSubContent>
              {availableTags.map((tag) => {
                const hasTag = item.tags?.some((t) => t.id === tag.id)
                return (
                  <ContextMenuCheckboxItem
                    key={tag.id}
                    className="text-xs"
                    checked={hasTag}
                    onClick={async (e) => {
                      e.stopPropagation()
                      try {
                        if (hasTag) {
                          // Delete tag if already exists
                          await removeWorkflowTag({
                            workflowId: item.id,
                            workspaceId,
                            tagId: tag.id,
                          })
                          toast({
                            title: "Tag removed",
                            description: `Successfully removed tag "${tag.name}" from workflow`,
                          })
                        } else {
                          // Add tag if doesn't exist
                          await addWorkflowTag({
                            workflowId: item.id,
                            workspaceId,
                            requestBody: {
                              tag_id: tag.id,
                            },
                          })
                          toast({
                            title: "Tag added",
                            description: `Successfully added tag "${tag.name}" to workflow`,
                          })
                        }
                      } catch (error) {
                        console.error("Failed to modify tag:", error)
                        toast({
                          title: "Error",
                          description: `Failed to ${hasTag ? "remove" : "add"} tag ${hasTag ? "from" : "to"} workflow`,
                          variant: "destructive",
                        })
                      }
                    }}
                  >
                    <div
                      className="mr-2 flex size-2 rounded-full"
                      style={{
                        backgroundColor: tag.color || undefined,
                      }}
                    />
                    <span>{tag.name}</span>
                  </ContextMenuCheckboxItem>
                )
              })}
            </ContextMenuSubContent>
          </ContextMenuPortal>
        </ContextMenuSub>
      ) : (
        <ContextMenuItem
          className="!bg-transparent text-xs !text-muted-foreground hover:cursor-not-allowed"
          onClick={(e) => e.stopPropagation()}
        >
          <TagsIcon className="mr-2 size-3.5" />
          <span>No tags available</span>
        </ContextMenuItem>
      )}
      <ContextMenuItem
        className="text-xs"
        disabled={!enabledExport}
        onClick={(e) => {
          e.stopPropagation()
          void handleExport(true)
        }}
      >
        <DownloadIcon className="mr-2 size-3.5" />
        Export draft
      </ContextMenuItem>
      <ContextMenuItem
        className="text-xs"
        disabled={!enabledExport}
        onClick={(e) => {
          e.stopPropagation()
          void handleExport(false)
        }}
      >
        <DownloadIcon className="mr-2 size-3.5" />
        Export saved
      </ContextMenuItem>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => {
          e.stopPropagation() // Prevent row click
          navigator.clipboard.writeText(item.id)
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy workflow ID
      </ContextMenuItem>
      <ContextMenuSeparator />
      <DeleteWorkflowAlertDialogTrigger asChild>
        <ContextMenuItem
          className="text-xs text-rose-500 focus:text-rose-600"
          onClick={(e) => {
            e.stopPropagation() // Prevent row click
            setSelectedWorkflow(item)
          }}
        >
          <Trash2 className="mr-2 size-3.5" />
          Delete
        </ContextMenuItem>
      </DeleteWorkflowAlertDialogTrigger>
    </ContextMenuGroup>
  )
}

export function FolderActions({
  item,
  setActiveDialog,
  setSelectedFolder,
}: {
  item: FolderDirectoryItem
  setActiveDialog: (activeDialog: ActiveDialog | null) => void
  setSelectedFolder: (folder: FolderDirectoryItem | null) => void
}) {
  return (
    <ContextMenuGroup>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation() // Prevent row click
          navigator.clipboard.writeText(item.id)
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy folder ID
      </ContextMenuItem>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation() // Prevent row click
          setSelectedFolder(item)
          setActiveDialog(ActiveDialog.FolderRename)
        }}
      >
        <Pencil className="mr-2 size-3.5" />
        Rename folder
      </ContextMenuItem>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation() // Prevent row click
          setSelectedFolder(item)
          setActiveDialog(ActiveDialog.FolderMove)
        }}
      >
        <FolderKanban className="mr-2 size-3.5" />
        Move folder
      </ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem
        className="text-xs text-rose-500 focus:text-rose-600"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation() // Prevent row click
          setSelectedFolder(item)
          setActiveDialog(ActiveDialog.FolderDelete)
        }}
      >
        <Trash2 className="mr-2 size-3.5" />
        Delete folder
      </ContextMenuItem>
    </ContextMenuGroup>
  )
}
