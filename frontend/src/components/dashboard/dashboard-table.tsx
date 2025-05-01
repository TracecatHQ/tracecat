"use client"

import React, { useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { TagRead, WorkflowReadMinimal } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Row } from "@tanstack/react-table"
import { format, formatDistanceToNow } from "date-fns"
import { CircleDot, Copy, FileJson2, Tags, Trash2 } from "lucide-react"

import { useOrgAppSettings, useTags, useWorkflowManager } from "@/lib/hooks"
import { capitalizeFirst } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  DeleteWorkflowAlertDialog,
  DeleteWorkflowAlertDialogTrigger,
} from "@/components/dashboard/delete-workflow-dialog"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { ExportMenuItem } from "@/components/export-workflow-dropdown-item"

export function WorkflowsDashboardTable() {
  const router = useRouter()
  const { appSettings } = useOrgAppSettings()
  const { workspaceId } = useWorkspace()
  const { user } = useAuth()
  const searchParams = useSearchParams()
  const queryTags = searchParams.getAll("tag") || undefined
  const {
    workflows,
    workflowsLoading,
    workflowsError,
    addWorkflowTag,
    removeWorkflowTag,
  } = useWorkflowManager({ tag: queryTags })
  const [selectedWorkflow, setSelectedWorkflow] =
    useState<WorkflowReadMinimal | null>(null)
  const { tags } = useTags(workspaceId)

  const handleOnClickRow = (row: Row<WorkflowReadMinimal>) => () => {
    // Link to workflow detail page
    console.debug("Clicked row", row)
    router.push(`/workspaces/${workspaceId}/workflows/${row.original.id}`)
  }
  const enabledExport = appSettings?.app_workflow_export_enabled
  return (
    <DeleteWorkflowAlertDialog
      selectedWorkflow={selectedWorkflow}
      setSelectedWorkflow={setSelectedWorkflow}
    >
      <TooltipProvider>
        <DataTable
          tableId={`${workspaceId}-${user?.id}:workflows-table`}
          initialColumnVisibility={{
            created_at: false,
          }}
          isLoading={workflowsLoading}
          error={workflowsError ?? undefined}
          data={workflows}
          emptyMessage="No workflows found."
          errorMessage="Error loading workflows."
          onClickRow={handleOnClickRow}
          columns={[
            {
              accessorKey: "title",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Title"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs text-foreground/80">
                  {row.getValue<WorkflowReadMinimal["title"]>("title")}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "Alias",
              accessorKey: "alias",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Alias"
                />
              ),
              cell: ({ getValue }) => {
                const alias = getValue<string | undefined>()
                if (!alias) {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      No alias
                    </span>
                  )
                }
                return (
                  <Badge
                    className="font-mono text-xs font-medium tracking-tighter text-foreground/80"
                    variant="secondary"
                  >
                    {alias}
                  </Badge>
                )
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "Last Edited",
              accessorKey: "updated_at",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Last Edited"
                />
              ),
              cell: ({ getValue }) => {
                const updatedAt = getValue<string | undefined>()
                if (!updatedAt) {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      No last edited
                    </span>
                  )
                }
                const updatedAtDate = new Date(updatedAt)
                return (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="text-xs text-muted-foreground">
                        {capitalizeFirst(
                          formatDistanceToNow(updatedAtDate, {
                            addSuffix: true,
                          })
                        )}
                      </div>
                    </TooltipTrigger>
                    <TooltipContent>
                      {format(updatedAtDate, "PPpp")}
                    </TooltipContent>
                  </Tooltip>
                )
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "Created",
              accessorKey: "created_at",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Created"
                />
              ),
              cell: ({ getValue }) => {
                const createdAt = getValue<string | undefined>()
                if (!createdAt) {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      No created
                    </span>
                  )
                }
                return (
                  <div className="text-xs text-muted-foreground">
                    {format(new Date(createdAt), "MMM d '·' p")}
                  </div>
                )
              },
              enableSorting: true,
            },
            {
              id: "Last Saved",
              accessorKey: "latest_definition.created_at",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Last Saved"
                />
              ),
              cell: ({ getValue }) => {
                const latestDefinitionCreatedAt = getValue<string | undefined>()
                if (!latestDefinitionCreatedAt) {
                  return (
                    <div className="flex items-center gap-1">
                      <CircleDot className="size-3 text-muted-foreground/70" />
                      <span className="text-xs text-muted-foreground/70">
                        Unsaved
                      </span>
                    </div>
                  )
                }
                const createdAt = new Date(latestDefinitionCreatedAt)
                return (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="text-xs text-muted-foreground">
                        {capitalizeFirst(
                          formatDistanceToNow(createdAt, {
                            addSuffix: true,
                          })
                        )}
                      </div>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      {format(createdAt, "PPpp")}
                    </TooltipContent>
                  </Tooltip>
                )
              },
              enableSorting: true,
            },
            {
              id: "Version",
              accessorKey: "latest_definition.version",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Version"
                />
              ),
              cell: ({ getValue }) => {
                const latestDefinitionVersion = getValue<number>()
                if (!latestDefinitionVersion) {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      No version
                    </span>
                  )
                }
                return (
                  <div className="text-xs font-normal text-muted-foreground">
                    {latestDefinitionVersion}
                  </div>
                )
              },
            },
            {
              id: "Tags",
              accessorKey: "tags",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Tags"
                />
              ),
              cell: ({ getValue }) => {
                const tags = getValue<WorkflowReadMinimal["tags"]>()
                return (
                  <div className="flex flex-wrap gap-1">
                    {tags?.length ? (
                      tags.map((tag) => <TagBadge key={tag.id} tag={tag} />)
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
                  </div>
                )
              },
              enableHiding: true,
            },
            {
              id: "actions",
              enableHiding: false,
              cell: ({ row }) => {
                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        className="size-8 p-0"
                        onClick={(e) => e.stopPropagation()} // Prevent row click
                      >
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      <DropdownMenuGroup>
                        <DropdownMenuItem
                          className="text-xs"
                          onClick={(e) => {
                            e.stopPropagation() // Prevent row click
                            navigator.clipboard.writeText(row.original.id)
                            toast({
                              title: "Workflow ID copied",
                              description: (
                                <div className="flex flex-col space-y-2">
                                  <span>
                                    Workflow ID copied for{" "}
                                    <b className="inline-block">
                                      {row.original.title}
                                    </b>
                                  </span>
                                  <span className="text-muted-foreground">
                                    ID: {row.original.id}
                                  </span>
                                </div>
                              ),
                            })
                          }}
                        >
                          <Copy className="mr-2 size-4" />
                          Copy workflow ID
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-xs"
                          onClick={(e) => {
                            e.stopPropagation() // Prevent row click
                            navigator.clipboard.writeText(row.original.title)
                            toast({
                              title: "Workflow alias copied",
                              description: (
                                <div className="flex flex-col space-y-2">
                                  <span>
                                    Workflow alias copied for{" "}
                                    <b className="inline-block">
                                      {row.original.title}
                                    </b>
                                  </span>
                                </div>
                              ),
                            })
                          }}
                        >
                          <Copy className="mr-2 size-4" />
                          Copy workflow alias
                        </DropdownMenuItem>
                        {tags && tags.length > 0 ? (
                          <DropdownMenuSub>
                            <DropdownMenuSubTrigger
                              className="text-xs"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Tags className="mr-2 size-4" />
                              Tags
                            </DropdownMenuSubTrigger>
                            <DropdownMenuPortal>
                              <DropdownMenuSubContent>
                                {/* No tags */}

                                {tags.map((tag) => {
                                  const hasTag = row.original.tags?.some(
                                    (t) => t.id === tag.id
                                  )
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
                                              workflowId: row.original.id,
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
                                              workflowId: row.original.id,
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
                                          console.error(
                                            "Failed to modify tag:",
                                            error
                                          )
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
                                          backgroundColor:
                                            tag.color || undefined,
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
                            <Tags className="mr-2 size-4" />
                            <span>No tags available</span>
                          </DropdownMenuItem>
                        )}
                        <ExportMenuItem
                          enabledExport={enabledExport}
                          format="json"
                          workspaceId={workspaceId}
                          workflowId={row.original.id}
                          icon={<FileJson2 className="mr-2 size-4" />}
                        />
                        <ExportMenuItem
                          enabledExport={enabledExport}
                          format="yaml"
                          workspaceId={workspaceId}
                          workflowId={row.original.id}
                          icon={<FileJson2 className="mr-2 size-4" />}
                        />
                        {/* Danger zone */}
                        <DeleteWorkflowAlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-xs text-rose-500 focus:text-rose-600"
                            onClick={(e) => {
                              e.stopPropagation() // Prevent row click
                              setSelectedWorkflow(row.original)
                              console.debug(
                                "Selected workflow to delete",
                                row.original
                              )
                            }}
                          >
                            <Trash2 className="mr-2 size-4" />
                            Delete
                          </DropdownMenuItem>
                        </DeleteWorkflowAlertDialogTrigger>
                      </DropdownMenuGroup>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )
              },
            },
          ]}
          toolbarProps={defaultToolbarProps}
        />
      </TooltipProvider>
    </DeleteWorkflowAlertDialog>
  )
}
const defaultToolbarProps: DataTableToolbarProps<WorkflowReadMinimal> = {
  filterProps: {
    placeholder: "Search workflows...",
    column: "title",
  },
}

export function TagBadge({ tag }: { tag: TagRead }) {
  return (
    <Badge
      key={tag.id}
      variant="secondary"
      className="text-xs"
      style={{
        backgroundColor: tag.color || undefined,
        color: tag.color ? "white" : undefined,
      }}
    >
      {tag.name}
    </Badge>
  )
}
