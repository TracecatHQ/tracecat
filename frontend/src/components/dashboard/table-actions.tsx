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
import { ExportMenuItem } from "@/components/export-workflow-dropdown-item"
import {
  DropdownMenuCheckboxItem,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
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

  return (
    <DropdownMenuGroup>
      <DropdownMenuItem
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
      </DropdownMenuItem>
      {item.alias && (
        <DropdownMenuItem
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
        </DropdownMenuItem>
      )}
      {showMoveToFolder && (
        <DropdownMenuItem
          className="text-xs"
          onClick={(e) => {
            e.stopPropagation() // Prevent row click
            setSelectedWorkflow(item)
            setActiveDialog?.(ActiveDialog.WorkflowMove)
          }}
        >
          <FolderUp className="mr-2 size-3.5" />
          Move to folder
        </DropdownMenuItem>
      )}
      {availableTags && availableTags.length > 0 ? (
        <DropdownMenuSub>
          <DropdownMenuSubTrigger
            className="text-xs"
            onClick={(e) => e.stopPropagation()}
          >
            <TagsIcon className="mr-2 size-3.5" />
            Tags
          </DropdownMenuSubTrigger>
          <DropdownMenuPortal>
            <DropdownMenuSubContent>
              {availableTags.map((tag) => {
                const hasTag = item.tags?.some((t) => t.id === tag.id)
                return (
                  <DropdownMenuCheckboxItem
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
                  </DropdownMenuCheckboxItem>
                )
              })}
            </DropdownMenuSubContent>
          </DropdownMenuPortal>
        </DropdownMenuSub>
      ) : (
        <DropdownMenuItem
          className="!bg-transparent text-xs !text-muted-foreground hover:cursor-not-allowed"
          onClick={(e) => e.stopPropagation()}
        >
          <TagsIcon className="mr-2 size-3.5" />
          <span>No tags available</span>
        </DropdownMenuItem>
      )}
      <ExportMenuItem
        enabledExport={enabledExport}
        format="yaml"
        workspaceId={workspaceId}
        workflowId={item.id}
        draft={true}
        label="Export draft"
        icon={<DownloadIcon className="mr-2 size-3.5" />}
      />
      <ExportMenuItem
        enabledExport={enabledExport}
        format="yaml"
        workspaceId={workspaceId}
        workflowId={item.id}
        draft={false}
        label="Export saved"
        icon={<DownloadIcon className="mr-2 size-3.5" />}
      />
      <DropdownMenuItem
        className="text-xs"
        onClick={(e) => {
          e.stopPropagation() // Prevent row click
          navigator.clipboard.writeText(item.id)
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy workflow ID
      </DropdownMenuItem>
      <DropdownMenuSeparator />
      <DeleteWorkflowAlertDialogTrigger asChild>
        <DropdownMenuItem
          className="text-xs text-rose-500 focus:text-rose-600"
          onClick={(e) => {
            e.stopPropagation() // Prevent row click
            setSelectedWorkflow(item)
          }}
        >
          <Trash2 className="mr-2 size-3.5" />
          Delete
        </DropdownMenuItem>
      </DeleteWorkflowAlertDialogTrigger>
    </DropdownMenuGroup>
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
    <DropdownMenuGroup>
      <DropdownMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation() // Prevent row click
          navigator.clipboard.writeText(item.id)
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy folder ID
      </DropdownMenuItem>
      <DropdownMenuItem
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
      </DropdownMenuItem>
      <DropdownMenuItem
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
      </DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuItem
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
      </DropdownMenuItem>
    </DropdownMenuGroup>
  )
}
