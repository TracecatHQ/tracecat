"use client"

import React from "react"
import {
  FolderDirectoryItem,
  WorkflowDirectoryItem,
  WorkflowReadMinimal,
} from "@/client"
import { useWorkspace } from "@/providers/workspace"
import {
  Copy,
  FileJson2,
  FolderKanban,
  FolderUp,
  Pencil,
  TagsIcon,
  Trash2,
} from "lucide-react"

import { useOrgAppSettings, useTags, useWorkflowManager } from "@/lib/hooks"
import {
  DropdownMenuCheckboxItem,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { DeleteWorkflowAlertDialogTrigger } from "@/components/dashboard/delete-workflow-dialog"
import { ViewMode } from "@/components/dashboard/folder-view-toggle"
import { ActiveDialog } from "@/components/dashboard/table-common"
import { ExportMenuItem } from "@/components/export-workflow-dropdown-item"

export function WorkflowActions({
  view,
  item,
  setSelectedWorkflow,
  setActiveDialog,
}: {
  view: ViewMode
  item: WorkflowDirectoryItem
  setSelectedWorkflow: (workflow: WorkflowReadMinimal) => void
  setActiveDialog?: (activeDialog: ActiveDialog | null) => void
}) {
  const { appSettings } = useOrgAppSettings()
  const { workspaceId } = useWorkspace()
  const { tags } = useTags(workspaceId)

  const { addWorkflowTag, removeWorkflowTag } = useWorkflowManager()
  const enabledExport = appSettings?.app_workflow_export_enabled ?? false

  return (
    <DropdownMenuGroup>
      <DropdownMenuItem
        className="text-xs"
        onClick={(e) => {
          e.stopPropagation() // Prevent row click
          navigator.clipboard.writeText(item.id)
          toast({
            title: "Workflow ID copied",
            description: (
              <div className="flex flex-col space-y-2">
                <span>
                  Workflow ID copied for{" "}
                  <b className="inline-block">{item.title}</b>
                </span>
                <span className="text-muted-foreground">ID: {item.id}</span>
              </div>
            ),
          })
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy workflow ID
      </DropdownMenuItem>
      {item.alias && (
        <DropdownMenuItem
          className="text-xs"
          onClick={(e) => {
            e.stopPropagation() // Prevent row click
            if (!item.alias) {
              return toast({
                title: "No alias",
                description: "This workflow has no alias",
              })
            }
            navigator.clipboard.writeText(item.alias)
            toast({
              title: "Workflow alias copied",
              description: (
                <div className="flex flex-col space-y-2">
                  <span>
                    Workflow alias copied for{" "}
                    <b className="inline-block">{item.title}</b>
                  </span>
                </div>
              ),
            })
          }}
        >
          <Copy className="mr-2 size-3.5" />
          Copy workflow alias
        </DropdownMenuItem>
      )}
      {view === ViewMode.Folders && (
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
      {tags && tags.length > 0 ? (
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
              {/* No tags */}

              {tags.map((tag) => {
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
        format="json"
        workspaceId={workspaceId}
        workflowId={item.id}
        icon={<FileJson2 className="mr-2 size-3.5" />}
      />
      <ExportMenuItem
        enabledExport={enabledExport}
        format="yaml"
        workspaceId={workspaceId}
        workflowId={item.id}
        icon={<FileJson2 className="mr-2 size-3.5" />}
      />
      {/* Danger zone */}
      <DeleteWorkflowAlertDialogTrigger asChild>
        <DropdownMenuItem
          className="text-xs text-rose-500 focus:text-rose-600"
          onClick={(e) => {
            e.stopPropagation() // Prevent row click
            setSelectedWorkflow(item)
            console.debug("Selected workflow to delete", item)
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
          toast({
            title: "Folder ID copied",
            description: (
              <div className="flex flex-col space-y-2">
                <span>
                  Folder ID copied for{" "}
                  <b className="inline-block">{item.name}</b>
                </span>
                <span className="text-muted-foreground">ID: {item.id}</span>
              </div>
            ),
          })
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

      {/* Danger zone */}
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
