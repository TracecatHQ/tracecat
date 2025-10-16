"use client"

import { useState } from "react"
import type { EntityFieldRead, FieldType } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import {
  FieldArchiveAlertDialog,
  FieldDeleteAlertDialog,
} from "@/components/entities/field-confirm-dialog"
import { EntityFieldActions } from "@/components/entities/table-actions"
import { ActiveDialog } from "@/components/entities/table-common"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getFieldTypeConfig } from "@/lib/data-type"

interface EntityFieldsTableProps {
  fields: EntityFieldRead[]
  onEditField?: (field: EntityFieldRead) => void
  onDeleteField?: (fieldId: string) => Promise<void>
  onDeactivateField?: (fieldId: string) => Promise<void>
  onReactivateField?: (fieldId: string) => Promise<void>
  isDeleting?: boolean
}

export function EntityFieldsTable({
  fields,
  onEditField,
  onDeleteField,
  onDeactivateField,
  onReactivateField,
  isDeleting,
}: EntityFieldsTableProps) {
  const [selectedField, setSelectedField] = useState<EntityFieldRead | null>(
    null
  )
  const [activeDialog, setActiveDialog] = useState<ActiveDialog | null>(null)

  return (
    <>
      <DataTable
        data={fields}
        emptyMessage="No fields found."
        columns={[
          {
            accessorKey: "display_name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Field"
              />
            ),
            cell: ({ row }) => (
              <div>
                <div className="text-sm font-medium">
                  {row.original.display_name}
                </div>
                <div className="text-xs text-muted-foreground">
                  {row.original.key}
                </div>
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "type",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Data type"
              />
            ),
            cell: ({ row }) => {
              const t = row.getValue<FieldType>("type")
              const cfg = getFieldTypeConfig(t)
              const Icon = cfg?.icon
              return (
                <Badge variant="secondary" className="text-xs">
                  {Icon && <Icon className="mr-1.5 h-3 w-3" />}
                  {cfg?.label || t}
                </Badge>
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
              const desc = row.getValue<string>("description")
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
            id: "actions",
            enableHiding: false,
            cell: ({ row }) => {
              return (
                <div className="flex justify-end">
                  <EntityFieldActions
                    field={row.original}
                    setSelectedField={(f) => setSelectedField(f)}
                    setActiveDialog={setActiveDialog}
                    onReactivateField={onReactivateField}
                    onEdit={onEditField}
                  />
                </div>
              )
            },
          },
        ]}
        toolbarProps={defaultToolbarProps}
      />
      {/* Confirmation dialogs */}
      {onDeactivateField && (
        <FieldArchiveAlertDialog
          open={activeDialog === ActiveDialog.FieldArchive}
          onOpenChange={() => setActiveDialog(null)}
          selectedField={selectedField}
          setSelectedField={setSelectedField}
          onConfirm={onDeactivateField}
          isPending={isDeleting}
        />
      )}
      {onDeleteField && (
        <FieldDeleteAlertDialog
          open={activeDialog === ActiveDialog.FieldDelete}
          onOpenChange={() => setActiveDialog(null)}
          selectedField={selectedField}
          setSelectedField={setSelectedField}
          onConfirm={onDeleteField}
          isPending={isDeleting}
        />
      )}
    </>
  )
}

const defaultToolbarProps: DataTableToolbarProps<EntityFieldRead> = {
  filterProps: {
    placeholder: "Filter fields...",
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
