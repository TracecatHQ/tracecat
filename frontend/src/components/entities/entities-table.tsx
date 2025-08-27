"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { CheckCircle, Copy, Eye, Pencil, Trash2, XCircle } from "lucide-react"
import { useRouter } from "next/navigation"
import { useState } from "react"
import type { EntityRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { EditEntityDialog } from "@/components/entities/edit-entity-dialog"
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
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getIconByName } from "@/lib/icons"
import { useWorkspaceId } from "@/providers/workspace-id"

interface EntitiesTableProps {
  entities: EntityRead[]
  fieldCounts: Record<string, number>
  onEditEntity?: (
    entity: EntityRead,
    data: { display_name: string; description?: string; icon?: string }
  ) => Promise<void>
  onDeleteEntity?: (entityId: string) => Promise<void>
  onDeactivateEntity: (entityId: string) => Promise<void>
  onReactivateEntity: (entityId: string) => Promise<void>
  isDeleting?: boolean
  isUpdating?: boolean
}

export function EntitiesTable({
  entities,
  fieldCounts,
  onEditEntity,
  onDeleteEntity,
  onDeactivateEntity,
  onReactivateEntity,
  isDeleting,
  isUpdating,
}: EntitiesTableProps) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const [selectedEntity, setSelectedEntity] = useState<EntityRead | null>(null)
  const [actionType, setActionType] = useState<"deactivate" | "delete" | null>(
    null
  )
  const [settingsDialogOpen, setSettingsDialogOpen] = useState(false)
  const [entityToEdit, setEntityToEdit] = useState<EntityRead | null>(null)

  return (
    <>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedEntity(null)
            setActionType(null)
          }
        }}
      >
        <DataTable
          data={entities}
          emptyMessage="No entities found."
          getRowHref={(row) =>
            row.original.is_active
              ? `/workspaces/${workspaceId}/entities/${row.original.id}`
              : undefined
          }
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
              cell: ({ row }) => {
                const IconComponent = row.original.icon
                  ? getIconByName(row.original.icon)
                  : undefined
                const initials =
                  row.original.display_name?.[0]?.toUpperCase() || "?"

                return (
                  <div className="flex items-center gap-2.5">
                    <Avatar className="size-7 shrink-0">
                      <AvatarFallback className="text-xs">
                        {IconComponent ? (
                          <IconComponent className="size-4" />
                        ) : (
                          initials
                        )}
                      </AvatarFallback>
                    </Avatar>
                    <div>
                      <div className="text-sm font-medium">
                        {row.original.display_name}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {row.original.key}
                      </div>
                    </div>
                  </div>
                )
              },
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
              cell: ({ row }) => {
                const desc = row.original.description as
                  | string
                  | null
                  | undefined
                if (!desc) return <div className="text-xs">-</div>
                const truncated =
                  desc.length > 140 ? `${desc.slice(0, 140)}...` : desc
                const needsTooltip = desc.length > 140
                return needsTooltip ? (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <div className="text-xs truncate max-w-[360px]">
                          {truncated}
                        </div>
                      </TooltipTrigger>
                      <TooltipContent>
                        <div className="max-w-xs text-xs whitespace-pre-wrap">
                          {desc}
                        </div>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  <div className="text-xs">{truncated}</div>
                )
              },
              enableSorting: false,
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
                <div className="text-xs">
                  {fieldCounts[row.original.id] || 0}
                </div>
              ),
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
                          <Copy className="mr-2 h-3 w-3" />
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
                            <Eye className="mr-2 h-3 w-3" />
                            View fields
                          </DropdownMenuItem>
                        )}
                        {row.original.is_active && onEditEntity && (
                          <DropdownMenuItem
                            onClick={(e) => {
                              e.stopPropagation()
                              setEntityToEdit(row.original)
                              setSettingsDialogOpen(true)
                            }}
                          >
                            <Pencil className="mr-2 h-3 w-3" />
                            Edit entity
                          </DropdownMenuItem>
                        )}
                        {row.original.is_active ? (
                          <>
                            <DropdownMenuSeparator />
                            <AlertDialogTrigger asChild>
                              <DropdownMenuItem
                                className="text-rose-500 focus:text-rose-600"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setSelectedEntity(row.original)
                                  setActionType("deactivate")
                                }}
                              >
                                <XCircle className="mr-2 h-3 w-3" />
                                Archive entity
                              </DropdownMenuItem>
                            </AlertDialogTrigger>
                          </>
                        ) : (
                          <>
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation()
                                // Reactivate without blocking UI
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
                              <CheckCircle className="mr-2 h-3 w-3" />
                              Restore entity
                            </DropdownMenuItem>
                            {onDeleteEntity && (
                              <>
                                <DropdownMenuSeparator />
                                <AlertDialogTrigger asChild>
                                  <DropdownMenuItem
                                    className="text-rose-500 focus:text-rose-600"
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      setSelectedEntity(row.original)
                                      setActionType("delete")
                                    }}
                                  >
                                    <Trash2 className="mr-2 h-3 w-3" />
                                    Delete entity
                                  </DropdownMenuItem>
                                </AlertDialogTrigger>
                              </>
                            )}
                          </>
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
            <AlertDialogTitle>
              {actionType === "delete"
                ? "Delete entity permanently"
                : "Archive entity"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {actionType === "delete" ? (
                <>
                  Are you sure you want to permanently delete the entity{" "}
                  <strong>{selectedEntity?.display_name}</strong>? This action
                  cannot be undone. All fields, records, and associated data
                  will be permanently deleted.
                </>
              ) : (
                <>
                  Are you sure you want to archive the entity{" "}
                  <strong>{selectedEntity?.display_name}</strong>? This will
                  hide the entity from normal use, but all data will be
                  preserved. You can restore the entity later.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant={actionType === "delete" ? "destructive" : "default"}
              onClick={async () => {
                if (selectedEntity) {
                  try {
                    if (actionType === "delete" && onDeleteEntity) {
                      await onDeleteEntity(selectedEntity.id)
                    } else if (actionType === "deactivate") {
                      await onDeactivateEntity(selectedEntity.id)
                    }
                    setSelectedEntity(null)
                    setActionType(null)
                  } catch (error) {
                    console.error(`Failed to ${actionType} entity:`, error)
                  }
                }
              }}
              disabled={isDeleting}
            >
              {actionType === "delete" ? "Delete Permanently" : "Archive"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {onEditEntity && (
        <EditEntityDialog
          entity={entityToEdit}
          open={settingsDialogOpen}
          onOpenChange={(open) => {
            setSettingsDialogOpen(open)
            if (!open) setEntityToEdit(null)
          }}
          onSubmit={async (data) => {
            if (entityToEdit) {
              await onEditEntity(entityToEdit, data)
              setSettingsDialogOpen(false)
              setEntityToEdit(null)
            }
          }}
          isPending={isUpdating}
        />
      )}
    </>
  )
}

const defaultToolbarProps: DataTableToolbarProps<EntityRead> = {
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
