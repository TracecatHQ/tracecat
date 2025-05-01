"use client"

import React, { useState } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import {
  ApiError,
  FolderDirectoryItem,
  TagRead,
  WorkflowReadMinimal,
} from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { Row } from "@tanstack/react-table"
import { format, formatDistanceToNow } from "date-fns"
import { CircleDot, FolderIcon, WorkflowIcon } from "lucide-react"

import { DirectoryItem, useGetDirectoryItems } from "@/lib/hooks"
import { capitalizeFirst, cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
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
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import { DeleteWorkflowAlertDialog } from "@/components/dashboard/delete-workflow-dialog"
import { FolderDeleteAlertDialog } from "@/components/dashboard/folder-delete-dialog"
import { FolderMoveDialog } from "@/components/dashboard/folder-move-dialog"
import { FolderRenameDialog } from "@/components/dashboard/folder-rename-dialog"
import {
  FolderViewToggle,
  ViewMode,
} from "@/components/dashboard/folder-view-toggle"
import {
  FolderActions,
  WorkflowActions,
} from "@/components/dashboard/table-actions"
import { ActiveDialog } from "@/components/dashboard/table-dialog-common"
import { WorkflowMoveDialog } from "@/components/dashboard/workflow-move-dialog"
import {
  DataTable,
  DataTableColumnHeader,
  DataTableToolbarProps,
} from "@/components/data-table"

const NO_DATA = "--" as const

export function WorkflowFoldersTable({
  view,
  setView,
}: {
  view: ViewMode
  setView: (view: ViewMode) => void
}) {
  const { workspaceId } = useWorkspace()
  const path = useSearchParams().get("path") || "/"
  const segments = path.split("/").filter(Boolean)

  // We should read the structure directly from the backend
  // i.e. the backend should return us the
  const { directoryItems, directoryItemsIsLoading, directoryItemsError } =
    useGetDirectoryItems(path, workspaceId)

  return (
    <div className="flex flex-col space-y-12">
      <div className="flex w-full">
        <div className="items-start space-y-3 text-left">
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbLink
                  asChild
                  className={cn(
                    "flex items-center",
                    segments.length > 0
                      ? "text-muted-foreground"
                      : "text-foreground"
                  )}
                >
                  <Link href={`/workspaces/${workspaceId}/workflows`}>
                    <h2 className="text-2xl font-semibold tracking-tight">
                      Workflows
                    </h2>
                  </Link>
                </BreadcrumbLink>
              </BreadcrumbItem>
              {/* Show folder path in breadcrumb */}
              {segments.length > 0 &&
                segments.map((segment, index) => {
                  // Build the path up to this segment
                  const pathToSegment = `/${segments.slice(0, index + 1).join("/")}`
                  const url = `/workspaces/${workspaceId}/workflows?path=${encodeURIComponent(pathToSegment)}`

                  return (
                    <React.Fragment key={index}>
                      <BreadcrumbSeparator className="text-xl">
                        {"/"}
                      </BreadcrumbSeparator>
                      <BreadcrumbItem>
                        <BreadcrumbLink
                          asChild
                          className={cn(
                            "flex items-center",
                            index === segments.length - 1
                              ? "text-foreground/80"
                              : "text-muted-foreground"
                          )}
                        >
                          {index === segments.length - 1 ? (
                            <h2 className="text-2xl font-semibold tracking-tight">
                              {segment}
                            </h2>
                          ) : (
                            <Link href={url}>
                              <h2 className="text-2xl font-semibold tracking-tight">
                                {segment}
                              </h2>
                            </Link>
                          )}
                        </BreadcrumbLink>
                      </BreadcrumbItem>
                    </React.Fragment>
                  )
                })}
            </BreadcrumbList>
          </Breadcrumb>
          <p className="text-md text-muted-foreground">
            Welcome back! Here are your workflows.
          </p>
        </div>
        <div className="ml-auto flex items-center space-x-2">
          <FolderViewToggle
            defaultView={view}
            onViewChange={(view) => setView(view)}
          />
          <CreateWorkflowButton view="folders" currentFolderPath={path} />
        </div>
      </div>
      <WorkflowsDashboardTable
        view={view}
        directoryItems={directoryItems}
        directoryItemsIsLoading={directoryItemsIsLoading}
        directoryItemsError={directoryItemsError}
      />
    </div>
  )
}

export function WorkflowsDashboardTable({
  view,
  directoryItems,
  directoryItemsIsLoading,
  directoryItemsError,
}: {
  view: ViewMode
  directoryItems: DirectoryItem[] | undefined
  directoryItemsIsLoading: boolean
  directoryItemsError: ApiError | null
}) {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { user } = useAuth()
  const [activeDialog, setActiveDialog] = useState<ActiveDialog | null>(null)
  const [selectedWorkflow, setSelectedWorkflow] =
    useState<WorkflowReadMinimal | null>(null)
  const [selectedFolder, setSelectedFolder] =
    useState<FolderDirectoryItem | null>(null)

  const handleOnClickRow = (row: Row<DirectoryItem>) => () => {
    // Link to workflow detail page
    console.debug("Clicked row", row)
    const item = row.original
    if (item.type === "workflow") {
      router.push(`/workspaces/${workspaceId}/workflows/${item.id}`)
    } else {
      router.push(
        `/workspaces/${workspaceId}/workflows?path=${encodeURIComponent(item.path)}`
      )
    }
  }
  return (
    <DeleteWorkflowAlertDialog
      selectedWorkflow={selectedWorkflow}
      setSelectedWorkflow={setSelectedWorkflow}
    >
      <TooltipProvider>
        <DataTable<DirectoryItem, unknown>
          tableId={`${workspaceId}-${user?.id}:workflows-table`}
          initialColumnVisibility={{
            created_at: false,
          }}
          isLoading={directoryItemsIsLoading}
          error={directoryItemsError}
          data={directoryItems}
          emptyMessage="No workflows found."
          errorMessage="Error loading workflows."
          onClickRow={handleOnClickRow}
          columns={[
            {
              accessorKey: "type",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Kind"
                />
              ),
              cell: ({ row }) => {
                const icon =
                  row.getValue<DirectoryItem["type"]>("type") === "workflow" ? (
                    <Tooltip>
                      <TooltipTrigger>
                        <WorkflowIcon
                          className="size-4 stroke-orange-400"
                          strokeWidth={2.5}
                        />
                      </TooltipTrigger>
                      <TooltipContent>Workflow</TooltipContent>
                    </Tooltip>
                  ) : (
                    <Tooltip>
                      <TooltipTrigger>
                        <FolderIcon
                          className="size-4 fill-sky-400/30 stroke-sky-400"
                          strokeWidth={2.5}
                        />
                      </TooltipTrigger>
                      <TooltipContent>Folder</TooltipContent>
                    </Tooltip>
                  )
                return <div className="flex items-center pl-2">{icon}</div>
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "name",
              accessorFn: (row: DirectoryItem) =>
                row.type === "workflow" ? row.title : row.name,
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Name"
                />
              ),
              cell: ({ getValue }) => (
                <div className="flex items-center gap-1 text-xs text-foreground/80">
                  {getValue<string>()}
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
              cell: ({ row, getValue }) => {
                if (row.original.type === "folder") {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      {NO_DATA}
                    </span>
                  )
                }

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
              cell: ({ getValue, row }) => {
                if (row.original.type === "folder") {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      {NO_DATA}
                    </span>
                  )
                }
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
              cell: ({ getValue, row }) => {
                if (row.original.type === "folder") {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      {NO_DATA}
                    </span>
                  )
                }
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
              accessorFn: (row: DirectoryItem) =>
                row.type === "workflow"
                  ? row.latest_definition?.created_at
                  : undefined,
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Last Saved"
                />
              ),
              cell: ({ getValue, row }) => {
                if (row.original.type === "folder") {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      {NO_DATA}
                    </span>
                  )
                }
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
              accessorFn: (row: DirectoryItem) =>
                row.type === "workflow"
                  ? row.latest_definition?.version
                  : undefined,
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Version"
                />
              ),
              cell: ({ getValue, row }) => {
                if (row.original.type === "folder") {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      {NO_DATA}
                    </span>
                  )
                }
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
              accessorFn: (row: DirectoryItem) =>
                row.type === "workflow" ? row.tags : undefined,
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Tags"
                />
              ),
              cell: ({ getValue, row }) => {
                if (row.original.type === "folder") {
                  return (
                    <span className="text-xs text-muted-foreground/70">
                      {NO_DATA}
                    </span>
                  )
                }
                const tags = getValue<TagRead[]>()
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
              id: "Actions",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Actions"
                />
              ),
              cell: ({ row }) => {
                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        className="size-6 p-0"
                        onClick={(e) => e.stopPropagation()} // Prevent row click
                      >
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      {row.original.type === "workflow" && (
                        <WorkflowActions
                          view={view}
                          item={row.original}
                          setSelectedWorkflow={setSelectedWorkflow}
                          setActiveDialog={setActiveDialog}
                        />
                      )}
                      {row.original.type === "folder" && (
                        <FolderActions
                          item={row.original}
                          setActiveDialog={setActiveDialog}
                          setSelectedFolder={setSelectedFolder}
                        />
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )
              },
            },
          ]}
          toolbarProps={defaultToolbarProps}
        />
      </TooltipProvider>
      <FolderDeleteAlertDialog
        open={activeDialog === ActiveDialog.FolderDelete}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
      <FolderRenameDialog
        open={activeDialog === ActiveDialog.FolderRename}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
      <WorkflowMoveDialog
        open={activeDialog === ActiveDialog.WorkflowMove}
        onOpenChange={() => setActiveDialog(null)}
        selectedWorkflow={selectedWorkflow}
        setSelectedWorkflow={setSelectedWorkflow}
      />
      <FolderMoveDialog
        open={activeDialog === ActiveDialog.FolderMove}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
    </DeleteWorkflowAlertDialog>
  )
}

const defaultToolbarProps: DataTableToolbarProps<DirectoryItem> = {
  filterProps: {
    placeholder: "Search workflows...",
    column: "name",
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
