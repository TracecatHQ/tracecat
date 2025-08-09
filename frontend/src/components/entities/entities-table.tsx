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
  onDeleteEntity: (entityId: string) => Promise<void>
  isDeleting?: boolean
}

export function EntitiesTable({
  entities,
  fieldCounts,
  onDeleteEntity,
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
                className="cursor-pointer"
                onClick={() => {
                  router.push(
                    `/workspaces/${workspaceId}/entities/${row.original.id}`
                  )
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
            id: "fields",
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
            enableSorting: false,
            enableHiding: false,
          },
          {
            id: "tags",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Tags"
              />
            ),
            cell: () => <div className="text-xs text-muted-foreground">0</div>,
            enableSorting: false,
            enableHiding: false,
          },
          {
            id: "cases",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Cases"
              />
            ),
            cell: () => <div className="text-xs text-muted-foreground">0</div>,
            enableSorting: false,
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
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          onClick={(e) => {
                            e.stopPropagation()
                            setSelectedEntity(row.original)
                          }}
                        >
                          Delete entity
                        </DropdownMenuItem>
                      </AlertDialogTrigger>
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
          <AlertDialogTitle>Delete Entity</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete the entity{" "}
            <strong>{selectedEntity?.display_name}</strong>? This action cannot
            be undone and will delete all fields and records associated with
            this entity.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedEntity) {
                try {
                  await onDeleteEntity(selectedEntity.id)
                  setSelectedEntity(null)
                } catch (error) {
                  console.error("Failed to delete entity:", error)
                }
              }
            }}
            disabled={isDeleting}
          >
            Delete
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
}
