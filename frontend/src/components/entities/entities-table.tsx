"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useRouter } from "next/navigation"
import { useState } from "react"
import type { EntityMetadataRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useWorkspace } from "@/providers/workspace"

interface EntitiesTableProps {
  entities: EntityMetadataRead[]
  fieldCounts: Record<string, number>
  onDeactivateEntity: (entityId: string) => Promise<void>
  onReactivateEntity: (entityId: string) => Promise<void>
  isDeleting?: boolean
}

export function EntitiesTable({
  entities,
  fieldCounts,
  onDeactivateEntity,
  onReactivateEntity,
  isDeleting,
}: EntitiesTableProps) {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const [selectedEntity, setSelectedEntity] =
    useState<EntityMetadataRead | null>(null)

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedEntity(null)
        }
      }}
    >
      <DataTable
        data={entities}
        columns={[
          {
            accessorKey: "display_name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Entity"
              />
            ),
            cell: ({ row }) => (
              <div
                className={row.original.is_active ? "cursor-pointer" : ""}
                onClick={() => {
                  if (row.original.is_active) {
                    router.push(
                      `/workspaces/${workspaceId}/entities/${row.original.id}`
                    )
                  }
                }}
              >
                <div className="font-medium text-sm">
                  {row.original.display_name}
                </div>
                <div className="text-xs text-muted-foreground">
                  {row.original.name}
                </div>
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            id: "status",
            accessorFn: (row) => (row.is_active ? "active" : "inactive"),
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Status"
              />
            ),
            cell: ({ row }) => (
              <Badge
                variant={row.original.is_active ? "default" : "secondary"}
                className="text-xs"
              >
                {row.original.is_active ? "Active" : "Inactive"}
              </Badge>
            ),
            filterFn: (row, _id, value: string[]) => {
              const status = row.original.is_active ? "active" : "inactive"
              return value.includes(status)
            },
            enableSorting: true,
            enableHiding: false,
            enableColumnFilter: true,
          },
          {
            id: "fields",
            accessorFn: (row) => fieldCounts[row.id] || 0,
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Fields"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs">{fieldCounts[row.original.id] || 0}</div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            id: "tags",
            accessorFn: () => 0, // Placeholder for future implementation
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Tags"
              />
            ),
            cell: () => <div className="text-xs text-muted-foreground">0</div>,
            enableSorting: true,
            enableHiding: false,
          },
          {
            id: "cases",
            accessorFn: () => 0, // Placeholder for future implementation
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Cases"
              />
            ),
            cell: () => <div className="text-xs text-muted-foreground">0</div>,
            enableSorting: true,
            enableHiding: false,
          },
          {
            id: "actions",
            enableHiding: false,
            cell: ({ row }) => {
              return (
                <div className="flex justify-end">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        className="size-8 p-0"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onClick={(e) => {
                          e.stopPropagation()
                          navigator.clipboard.writeText(row.original.id)
                        }}
                      >
                        Copy entity ID
                      </DropdownMenuItem>
                      {row.original.is_active && (
                        <DropdownMenuItem
                          onClick={(e) => {
                            e.stopPropagation()
                            router.push(
                              `/workspaces/${workspaceId}/entities/${row.original.id}`
                            )
                          }}
                        >
                          View entity
                        </DropdownMenuItem>
                      )}
                      {row.original.is_active ? (
                        <AlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            onClick={(e) => {
                              e.stopPropagation()
                              setSelectedEntity(row.original)
                            }}
                          >
                            Deactivate entity
                          </DropdownMenuItem>
                        </AlertDialogTrigger>
                      ) : (
                        <DropdownMenuItem
                          onClick={(e) => {
                            e.stopPropagation()
                            // Don't await here to avoid React state update issues
                            onReactivateEntity(row.original.id).catch(
                              (error) => {
                                console.error(
                                  "Failed to reactivate entity:",
                                  error
                                )
                              }
                            )
                          }}
                        >
                          Reactivate entity
                        </DropdownMenuItem>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              )
            },
          },
        ]}
        toolbarProps={defaultToolbarProps}
      />
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Deactivate entity</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to deactivate the entity{" "}
            <strong>{selectedEntity?.display_name}</strong>? This will hide the
            entity from normal use, but all data will be preserved. You can
            reactivate the entity later.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedEntity) {
                try {
                  await onDeactivateEntity(selectedEntity.id)
                  setSelectedEntity(null)
                } catch (error) {
                  console.error("Failed to deactivate entity:", error)
                }
              }
            }}
            disabled={isDeleting}
          >
            Deactivate
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const defaultToolbarProps: DataTableToolbarProps<EntityMetadataRead> = {
  filterProps: {
    placeholder: "Filter entities...",
    column: "display_name",
  },
  fields: [
    {
      column: "status",
      title: "Status",
      options: [
        { label: "Active", value: "active" },
        { label: "Inactive", value: "inactive" },
      ],
    },
  ],
}
