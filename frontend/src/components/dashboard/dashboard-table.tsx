"use client"

import React, { useState } from "react"
import { useRouter } from "next/navigation"
import { WorkflowMetadataResponse } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Row } from "@tanstack/react-table"

import { exportWorkflow, handleExportError } from "@/lib/export"
import { useWorkflowManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
  const { workflows, workflowsLoading, workflowsError } = useWorkflowManager()
  const [selectedWorkflow, setSelectedWorkflow] =
    useState<WorkflowMetadataResponse | null>(null)

  const handleOnClickRow = (row: Row<WorkflowMetadataResponse>) => () => {
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
                {row.getValue<WorkflowMetadataResponse["title"]>("title")}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "description",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Description"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs text-foreground/80">
                {row.getValue<WorkflowMetadataResponse["description"]>(
                  "description"
                ) || "-"}
              </div>
            ),
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
                row.getValue<WorkflowMetadataResponse["status"]>("status")
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
