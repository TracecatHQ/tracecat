"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { format } from "date-fns"
import { PencilIcon } from "lucide-react"
import { useState } from "react"
import type { CaseFieldReadMinimal } from "@/client"
import { EditCustomFieldDialog } from "@/components/cases/edit-custom-field-dialog"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
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
import type { SqlType } from "@/lib/data-type"

interface CustomFieldsTableProps {
  fields: CaseFieldReadMinimal[]
  onDeleteField: (fieldId: string) => Promise<void>
  isDeleting?: boolean
}

export function CustomFieldsTable({
  fields,
  onDeleteField,
  isDeleting,
}: CustomFieldsTableProps) {
  const [selectedField, setSelectedField] =
    useState<CaseFieldReadMinimal | null>(null)
  const [editingField, setEditingField] = useState<CaseFieldReadMinimal | null>(
    null
  )

  return (
    <>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedField(null)
          }
        }}
      >
        <DataTable
          data={fields.filter((field) => !field.reserved)}
          columns={[
            {
              accessorKey: "id",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Field ID"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs text-foreground/80">
                  {row.getValue<CaseFieldReadMinimal["id"]>("id")}
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
              cell: ({ row }) => (
                <SqlTypeBadge
                  type={
                    row.getValue<CaseFieldReadMinimal["type"]>(
                      "type"
                    ) as SqlType
                  }
                />
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "default",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Default value"
                />
              ),
              cell: ({ row }) => {
                const defaultValue =
                  row.getValue<CaseFieldReadMinimal["default"]>("default")
                const fieldType = row.original.type

                // Handle SELECT/MULTI_SELECT as badges
                if (fieldType === "SELECT" || fieldType === "MULTI_SELECT") {
                  let parsedValues: string[] = []
                  if (defaultValue) {
                    if (fieldType === "MULTI_SELECT") {
                      try {
                        const parsed = JSON.parse(defaultValue)
                        if (Array.isArray(parsed)) {
                          parsedValues = parsed.filter(
                            (item): item is string => typeof item === "string"
                          )
                        }
                      } catch {
                        parsedValues = [defaultValue]
                      }
                    } else {
                      parsedValues = [defaultValue]
                    }
                  }

                  if (parsedValues.length > 0) {
                    return (
                      <div className="flex flex-wrap gap-1">
                        {parsedValues.map((item, idx) => (
                          <Badge
                            key={`default-${item}-${idx}`}
                            variant="secondary"
                            className="text-[11px]"
                          >
                            {item}
                          </Badge>
                        ))}
                      </div>
                    )
                  }
                  return <div className="text-xs">-</div>
                }

                // Handle TIMESTAMP/TIMESTAMPTZ with date formatting
                const parsedDate =
                  typeof defaultValue === "string" &&
                  defaultValue &&
                  (fieldType === "TIMESTAMP" || fieldType === "TIMESTAMPTZ")
                    ? new Date(defaultValue)
                    : null
                const isValidDate =
                  parsedDate && !Number.isNaN(parsedDate.getTime())
                    ? parsedDate
                    : null

                return (
                  <div className="text-xs">
                    {isValidDate
                      ? format(isValidDate, "MMM d yyyy '·' p")
                      : defaultValue || "-"}
                  </div>
                )
              },
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
                          Copy field ID
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => {
                            setEditingField(row.original)
                          }}
                        >
                          <PencilIcon className="mr-2 size-3" />
                          Edit field
                        </DropdownMenuItem>
                        <AlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            onClick={() => {
                              setSelectedField(row.original)
                            }}
                          >
                            Delete field
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
            <AlertDialogTitle>Delete Field</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the field{" "}
              <strong>{selectedField?.id}</strong>? This action cannot be undone
              and will delete all existing values for this field across all
              cases.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                if (selectedField) {
                  try {
                    await onDeleteField(selectedField.id)
                    setSelectedField(null)
                  } catch (error) {
                    console.error("Failed to delete field:", error)
                    // Keep dialog open so user can see error and retry
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
      <EditCustomFieldDialog
        open={editingField !== null}
        field={editingField}
        onOpenChange={(open) => {
          if (!open) {
            setEditingField(null)
          }
        }}
      />
    </>
  )
}

const defaultToolbarProps: DataTableToolbarProps<CaseFieldReadMinimal> = {
  filterProps: {
    placeholder: "Filter fields by ID...",
    column: "id",
  },
}
