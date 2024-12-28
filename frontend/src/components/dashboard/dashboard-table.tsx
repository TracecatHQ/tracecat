"use client"

import React, { useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { TagRead, WorkflowReadMinimal } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Row } from "@tanstack/react-table"

import { exportWorkflow, handleExportError } from "@/lib/export"
import { useTags, useWorkflowManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import {
  DeleteWorkflowAlertDialog,
  DeleteWorkflowAlertDialogTrigger,
} from "@/components/dashboard/delete-workflow-dialog"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/table"

export function WorkflowsDashboardTable() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
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
  return (
    <DeleteWorkflowAlertDialog
      selectedWorkflow={selectedWorkflow}
      setSelectedWorkflow={setSelectedWorkflow}
    >
      <DataTable
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
            accessorKey: "alias",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Alias"
              />
            ),
            cell: ({ row }) => {
              const alias = row.getValue<WorkflowReadMinimal["alias"]>("alias")
              if (!alias) {
                return (
                  <span className="text-xs text-muted-foreground/80">
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
            accessorKey: "status",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Status"
              />
            ),
            cell: ({ row }) => {
              const status =
                row.getValue<WorkflowReadMinimal["status"]>("status")
              return (
                <div className="flex-auto space-x-4 text-xs">
                  <div className="ml-auto flex items-center space-x-2">
                    <span className="text-xs capitalize text-muted-foreground">
                      {status}
                    </span>
                    <span
                      className={cn(
                        "flex size-2 rounded-full",
                        status === "online"
                          ? "bg-emerald-500/80"
                          : "bg-gray-400"
                      )}
                    />
                  </div>
                </div>
              )
            },
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "tags",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Tags"
              />
            ),
            cell: ({ row }) => (
              <div className="flex flex-wrap gap-1">
                {row.getValue<WorkflowReadMinimal["tags"]>("tags")?.length ? (
                  row
                    .getValue<WorkflowReadMinimal["tags"]>("tags")
                    ?.map((tag) => <TagBadge key={tag.id} tag={tag} />)
                ) : (
                  <span className="text-xs text-muted-foreground">-</span>
                )}
              </div>
            ),
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
                  <DropdownMenuContent align="end">
                    <DropdownMenuGroup>
                      <DropdownMenuLabel className="text-xs">
                        Actions
                      </DropdownMenuLabel>
                      <DropdownMenuSeparator />
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
                        Copy Workflow ID
                      </DropdownMenuItem>
                      {tags && tags.length > 0 ? (
                        <DropdownMenuSub>
                          <DropdownMenuSubTrigger
                            className="text-xs"
                            onClick={(e) => e.stopPropagation()}
                          >
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
                          <span>No tags available</span>
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuItem
                        className="text-xs"
                        onClick={async (e) => {
                          e.stopPropagation() // Prevent row click

                          try {
                            await exportWorkflow({
                              workspaceId,
                              workflowId: row.original.id,
                              format: "json",
                            })
                          } catch (error) {
                            console.error(
                              "Failed to download workflow definition:",
                              error
                            )
                            toast(handleExportError(error as Error))
                          }
                        }}
                      >
                        Export to JSON
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        className="text-xs"
                        onClick={async (e) => {
                          e.stopPropagation() // Prevent row click

                          try {
                            await exportWorkflow({
                              workspaceId,
                              workflowId: row.original.id,
                              format: "yaml",
                            })
                          } catch (error) {
                            console.error(
                              "Failed to download workflow definition:",
                              error
                            )
                            toast(handleExportError(error as Error))
                          }
                        }}
                      >
                        Export to YAML
                      </DropdownMenuItem>

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
    </DeleteWorkflowAlertDialog>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
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
