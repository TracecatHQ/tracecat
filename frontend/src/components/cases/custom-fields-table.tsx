"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { format } from "date-fns"
import { useState } from "react"
import type { CaseFieldRead } from "@/client"
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
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { SqlType } from "@/lib/data-type"

interface CustomFieldsTableProps {
  fields: CaseFieldRead[]
  onDeleteField: (fieldId: string) => Promise<void>
  isDeleting?: boolean
}

export function CustomFieldsTable({
  fields,
  onDeleteField,
  isDeleting,
}: CustomFieldsTableProps) {
  const [selectedField, setSelectedField] = useState<CaseFieldRead | null>(null)

  return (
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
                {row.getValue<CaseFieldRead["id"]>("id")}
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
                type={row.getValue<CaseFieldRead["type"]>("type") as SqlType}
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
                row.getValue<CaseFieldRead["default"]>("default")
              const fieldType = row.original.type
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
            and will delete all existing values for this field across all cases.
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
  )
}

const defaultToolbarProps: DataTableToolbarProps<CaseFieldRead> = {
  filterProps: {
    placeholder: "Filter fields by ID...",
    column: "id",
  },
}
