"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { Row } from "@tanstack/react-table"
import { format, formatDistanceToNow } from "date-fns"
import { CircleDot } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"
import type { WorkflowReadMinimal } from "@/client"
import { DeleteWorkflowAlertDialog } from "@/components/dashboard/delete-workflow-dialog"
import { ViewMode } from "@/components/dashboard/folder-view-toggle"
import { WorkflowActions } from "@/components/dashboard/table-actions"
import { NO_DATA } from "@/components/dashboard/table-common"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { TagBadge } from "@/components/tag-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useWorkflowsPagination } from "@/hooks/pagination/use-workflows-pagination"
import { useAuth } from "@/hooks/use-auth"
import { capitalizeFirst } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

export function WorkflowsTagsTable() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { user } = useAuth()
  const searchParams = useSearchParams()
  const queryTags = searchParams?.getAll("tag")
  const tags = queryTags && queryTags.length > 0 ? queryTags : undefined
  const [pageSize, setPageSize] = useState(20)
  const {
    data: workflows,
    isLoading: workflowsLoading,
    error: workflowsError,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
    totalEstimate,
    startItem,
    endItem,
  } = useWorkflowsPagination({
    workspaceId,
    tags,
    limit: pageSize,
  })
  const [selectedWorkflow, setSelectedWorkflow] =
    useState<WorkflowReadMinimal | null>(null)

  const handleOnClickRow = (row: Row<WorkflowReadMinimal>) => () => {
    router.push(`/workspaces/${workspaceId}/workflows/${row.original.id}`)
  }

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
          getRowHref={(row) =>
            `/workspaces/${workspaceId}/workflows/${row.original.id}`
          }
          serverSidePagination={{
            currentPage,
            hasNextPage,
            hasPreviousPage,
            pageSize,
            totalEstimate,
            startItem,
            endItem,
            onNextPage: goToNextPage,
            onPreviousPage: goToPreviousPage,
            onFirstPage: goToFirstPage,
            onPageSizeChange: setPageSize,
            isLoading: workflowsLoading,
          }}
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
              enableSorting: false,
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
              enableSorting: false,
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
              enableSorting: false,
            },
            {
              id: "Updated",
              accessorKey: "updated_at",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Updated"
                />
              ),
              cell: ({ getValue }) => {
                const updatedAt = getValue<string | undefined>()
                if (!updatedAt) {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      No updated
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
              enableSorting: false,
              enableHiding: false,
            },
            {
              id: "Last published",
              accessorKey: "latest_definition.created_at",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Last published"
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
              enableSorting: false,
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
                      <span className="text-xs text-muted-foreground">
                        {NO_DATA}
                      </span>
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
                        className="size-6 p-0"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      <WorkflowActions
                        view={ViewMode.Tags}
                        item={{
                          type: "workflow",
                          ...row.original,
                        }}
                        setSelectedWorkflow={setSelectedWorkflow}
                      />
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
