"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  BookA,
  Braces,
  Brackets,
  Calendar,
  CalendarClock,
  CheckCircle,
  Copy,
  DecimalsArrowRight,
  Hash,
  Link,
  ListOrdered,
  ListTodo,
  Network,
  Pencil,
  SquareCheck,
  ToggleLeft,
  Trash2,
  Type,
  XCircle,
} from "lucide-react"
import { useState } from "react"
import type { EntityRead, FieldMetadataRead, FieldType } from "@/client"
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

const fieldTypeConfig: Partial<
  Record<FieldType, { label: string; icon: React.ElementType }>
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
  ARRAY_TEXT: { label: "Text array", icon: BookA },
  ARRAY_INTEGER: { label: "Integer array", icon: ListOrdered },
  ARRAY_NUMBER: { label: "Number array", icon: Brackets },
  RELATION_ONE_TO_ONE: { label: "One to one", icon: Link },
  RELATION_ONE_TO_MANY: { label: "One to many", icon: Network },
  RELATION_MANY_TO_ONE: { label: "Many to one", icon: Link },
  RELATION_MANY_TO_MANY: { label: "Many to many", icon: Network },
}

interface EntityFieldsTableProps {
  fields: FieldMetadataRead[]
  entities?: EntityRead[]
  currentEntityName?: string
  onEditField?: (field: FieldMetadataRead) => void
  onDeleteField?: (fieldId: string) => Promise<void>
  onDeactivateField?: (fieldId: string) => Promise<void>
  onReactivateField?: (fieldId: string) => Promise<void>
  isDeleting?: boolean
}

export function EntityFieldsTable({
  fields,
  entities,
  currentEntityName,
  onEditField,
  onDeleteField,
  onDeactivateField,
  onReactivateField,
  isDeleting,
}: EntityFieldsTableProps) {
  const [selectedField, setSelectedField] = useState<FieldMetadataRead | null>(
    null
  )
  const [actionType, setActionType] = useState<"delete" | "deactivate" | null>(
    null
  )

  return (
    <TooltipProvider>
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
                  <div className="font-medium text-sm">
                    {row.original.display_name}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {row.original.field_key}
                  </div>
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "field_type",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Data type"
                />
              ),
              cell: ({ row }) => {
                const fieldType = row.getValue<FieldType>("field_type")
                const config = fieldTypeConfig[fieldType]
                const Icon = config?.icon
                const field = row.original

                // Get target entity name (slug) for relation fields
                let targetEntityName: string | undefined
                if (
                  (fieldType === "RELATION_ONE_TO_ONE" ||
                    fieldType === "RELATION_ONE_TO_MANY" ||
                    fieldType === "RELATION_MANY_TO_ONE" ||
                    fieldType === "RELATION_MANY_TO_MANY") &&
                  field.target_entity_id &&
                  entities
                ) {
                  const targetEntity = entities.find(
                    (e) => e.id === field.target_entity_id
                  )
                  // Use the entity's name (slug) instead of display_name
                  targetEntityName = targetEntity?.name
                }

                // Build label for badge
                let badgeLabel = config?.label || fieldType
                if (targetEntityName) {
                  badgeLabel = `${config?.label} ${targetEntityName}`
                }

                // Build tooltip content for relation fields
                const getTooltipContent = () => {
                  if (!targetEntityName || !currentEntityName) return null

                  if (fieldType === "RELATION_ONE_TO_ONE") {
                    return `One ${currentEntityName} to one ${targetEntityName}`
                  } else if (fieldType === "RELATION_ONE_TO_MANY") {
                    return `One ${currentEntityName} to many ${targetEntityName}`
                  } else if (fieldType === "RELATION_MANY_TO_ONE") {
                    return `Many ${currentEntityName} to one ${targetEntityName}`
                  } else if (fieldType === "RELATION_MANY_TO_MANY") {
                    return `Many ${currentEntityName} to many ${targetEntityName}`
                  }
                  return null
                }

                const tooltipContent = getTooltipContent()

                const badge = (
                  <Badge variant="secondary" className="text-xs">
                    {Icon && <Icon className="mr-1.5 h-3 w-3" />}
                    {badgeLabel}
                  </Badge>
                )

                if (tooltipContent) {
                  return (
                    <Tooltip>
                      <TooltipTrigger asChild>{badge}</TooltipTrigger>
                      <TooltipContent>{tooltipContent}</TooltipContent>
                    </Tooltip>
                  )
                }

                return badge
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "default",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Default"
                />
              ),
              cell: ({ row }) => {
                const defaultValue = row.original.default_value
                const fieldType = row.original.field_type

                if (defaultValue === null || defaultValue === undefined) {
                  return (
                    <span className="text-xs text-muted-foreground">-</span>
                  )
                }

                // Format default value based on type
                let displayValue: string
                if (fieldType === "BOOL") {
                  displayValue = defaultValue ? "true" : "false"
                } else if (
                  fieldType === "MULTI_SELECT" &&
                  Array.isArray(defaultValue)
                ) {
                  displayValue = defaultValue.join(", ")
                } else if (typeof defaultValue === "object") {
                  displayValue = JSON.stringify(defaultValue)
                } else {
                  displayValue = String(defaultValue)
                }

                return (
                  <span className="text-xs text-muted-foreground">
                    {displayValue}
                  </span>
                )
              },
              enableSorting: false,
              enableHiding: true,
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
                <div className="text-xs">
                  {row.getValue<string>("description") || "-"}
                </div>
              ),
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
                                Deactivate field
                              </DropdownMenuItem>
                            </AlertDialogTrigger>
                          </>
                        )}
                        {!row.original.is_active && onReactivateField && (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              onClick={() =>
                                void onReactivateField(row.original.id)
                              }
                            >
                              <CheckCircle className="mr-2 h-3 w-3" />
                              Reactivate field
                            </DropdownMenuItem>
                          </>
                        )}
                        {!row.original.is_active && onDeleteField && (
                          <>
                            {!onReactivateField && <DropdownMenuSeparator />}
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
                : "Deactivate field"}
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
                  Are you sure you want to deactivate the field{" "}
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
                    } else if (
                      actionType === "deactivate" &&
                      onDeactivateField
                    ) {
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
              {actionType === "delete" ? "Delete Permanently" : "Deactivate"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TooltipProvider>
  )
}

const defaultToolbarProps: DataTableToolbarProps<FieldMetadataRead> = {
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
