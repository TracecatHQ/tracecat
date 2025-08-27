"use client"

// no router needed for actions; only building href strings
import { useState } from "react"
import type { EntityRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { EditEntityDialog } from "@/components/entities/edit-entity-dialog"
import {
  EntityArchiveAlertDialog,
  EntityDeleteAlertDialog,
} from "@/components/entities/entity-confirm-dialog"
import { EntityActions } from "@/components/entities/table-actions"
import { ActiveDialog } from "@/components/entities/table-common"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
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
  const workspaceId = useWorkspaceId()
  const [selectedEntity, setSelectedEntity] = useState<EntityRead | null>(null)
  const [activeDialog, setActiveDialog] = useState<ActiveDialog | null>(null)
  const [settingsDialogOpen, setSettingsDialogOpen] = useState(false)
  const [entityToEdit, setEntityToEdit] = useState<EntityRead | null>(null)

  return (
    <>
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
              const desc = row.original.description as string | null | undefined
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
              <div className="text-xs">{fieldCounts[row.original.id] || 0}</div>
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
                  <EntityActions
                    entity={row.original}
                    setSelectedEntity={(e) => setSelectedEntity(e)}
                    setActiveDialog={setActiveDialog}
                    onReactivateEntity={onReactivateEntity}
                    onEdit={
                      onEditEntity
                        ? (e) => {
                            setEntityToEdit(e)
                            setSettingsDialogOpen(true)
                          }
                        : undefined
                    }
                  />
                </div>
              )
            },
          },
        ]}
        toolbarProps={defaultToolbarProps}
      />

      {/* Confirmation dialogs */}
      <EntityArchiveAlertDialog
        open={activeDialog === ActiveDialog.EntityArchive}
        onOpenChange={() => setActiveDialog(null)}
        selectedEntity={selectedEntity}
        setSelectedEntity={setSelectedEntity}
        onConfirm={onDeactivateEntity}
        isPending={isDeleting}
      />
      {onDeleteEntity && (
        <EntityDeleteAlertDialog
          open={activeDialog === ActiveDialog.EntityDelete}
          onOpenChange={() => setActiveDialog(null)}
          selectedEntity={selectedEntity}
          setSelectedEntity={setSelectedEntity}
          onConfirm={onDeleteEntity}
          isPending={isDeleting}
        />
      )}

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
