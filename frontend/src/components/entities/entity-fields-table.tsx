"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  Braces,
  Calendar,
  CalendarClock,
  CheckCircle,
  Copy,
  DecimalsArrowRight,
  Hash,
  ListTodo,
  Pencil,
  SquareCheck,
  ToggleLeft,
  Trash2,
  Type,
  XCircle,
} from "lucide-react"
import { useState } from "react"
import type { EntityFieldRead, FieldType } from "@/client"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const fieldTypeConfig: Record<
  FieldType,
  { label: string; icon: React.ElementType }
> = {
  TEXT: { label: "Text", icon: Type },
  INTEGER: { label: "Integer", icon: Hash },
  NUMBER: { label: "Number", icon: DecimalsArrowRight },
  BOOL: { label: "Boolean", icon: ToggleLeft },
  JSON: { label: "JSON", icon: Braces },
  DATE: { label: "Date", icon: Calendar },
  DATETIME: { label: "Date and time", icon: CalendarClock },
  SELECT: { label: "Select", icon: SquareCheck },
  MULTI_SELECT: { label: "Multi-select", icon: ListTodo },
}

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
  const [actionType, setActionType] = useState<"delete" | "deactivate" | null>(
    null
  )

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedField(null)
          setActionType(null)
        }
      }}
    >
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
              const cfg = fieldTypeConfig[t]
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
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onClick={() =>
                          navigator.clipboard.writeText(row.original.id)
                        }
                      >
                        <Copy className="mr-2 h-3 w-3" />
                        Copy field ID
                      </DropdownMenuItem>
                      {onEditField && (
                        <DropdownMenuItem
                          onClick={() => onEditField(row.original)}
                        >
                          <Pencil className="mr-2 h-3 w-3" />
                          Edit field
                        </DropdownMenuItem>
                      )}
                      {row.original.is_active && onDeactivateField && (
                        <>
                          <DropdownMenuSeparator />
                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => {
                                setSelectedField(row.original)
                                setActionType("deactivate")
                              }}
                            >
                              <XCircle className="mr-2 h-3 w-3" />
                              Archive field
                            </DropdownMenuItem>
                          </AlertDialogTrigger>
                        </>
                      )}
                      {!row.original.is_active && onReactivateField && (
                        <DropdownMenuItem
                          onClick={() =>
                            void onReactivateField(row.original.id)
                          }
                        >
                          <CheckCircle className="mr-2 h-3 w-3" />
                          Restore field
                        </DropdownMenuItem>
                      )}
                      {!row.original.is_active && onDeleteField && (
                        <>
                          <DropdownMenuSeparator />
                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => {
                                setSelectedField(row.original)
                                setActionType("delete")
                              }}
                            >
                              <Trash2 className="mr-2 h-3 w-3" />
                              Delete field
                            </DropdownMenuItem>
                          </AlertDialogTrigger>
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
              ? "Delete field permanently"
              : "Archive field"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {actionType === "delete" ? (
              <>
                Are you sure you want to permanently delete the field{" "}
                <strong>{selectedField?.display_name}</strong>? This action
                cannot be undone and will delete all existing values for this
                field across all records.
              </>
            ) : (
              <>
                Are you sure you want to archive the field{" "}
                <strong>{selectedField?.display_name}</strong>? The field will
                be hidden but data will be preserved.
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant={actionType === "delete" ? "destructive" : "default"}
            onClick={async () => {
              if (selectedField) {
                try {
                  if (actionType === "delete" && onDeleteField) {
                    await onDeleteField(selectedField.id)
                  } else if (actionType === "deactivate" && onDeactivateField) {
                    await onDeactivateField(selectedField.id)
                  }
                  setSelectedField(null)
                  setActionType(null)
                } catch (error) {
                  console.error(`Failed to ${actionType} field:`, error)
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
