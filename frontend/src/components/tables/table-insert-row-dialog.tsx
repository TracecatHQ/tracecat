"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useParams } from "next/navigation"
import { useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { TableColumnRead } from "@/client"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import {
  DynamicInput,
  getColumnOptions,
} from "@/components/tables/dynamic-column-input"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import type { SqlType } from "@/lib/data-type"
import { useGetTable, useInsertRow } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

// Update the schema to be dynamic based on table columns
const createInsertTableRowSchema = (columns: readonly TableColumnRead[]) => {
  const columnValidations: Record<string, z.ZodType> = {}

  columns.forEach((column) => {
    const normalizedType = column.type.toUpperCase()
    const options = getColumnOptions(column)

    // Add validation based on SQL type - handle select/multi-select as well
    switch (normalizedType) {
      case "TEXT":
        columnValidations[column.name] = z
          .string()
          .min(1, `${column.name} is required`)
        break
      case "INTEGER":
        columnValidations[column.name] = z
          .string()
          .trim()
          .min(1, `${column.name} is required`)
          .refine(
            (val) => {
              const n = Number(val)
              return Number.isInteger(n)
            },
            { message: `${column.name} must be an integer` }
          )
          .transform((val) => Number(val))
        break
      case "NUMERIC":
        columnValidations[column.name] = z
          .string()
          .trim()
          .min(1, `${column.name} is required`)
          .refine(
            (val) => {
              const n = Number(val)
              return Number.isFinite(n)
            },
            { message: `${column.name} must be a number` }
          )
          .transform((val) => Number(val))
        break
      case "BOOLEAN":
        // Accept string inputs and transform to boolean
        columnValidations[column.name] = z
          .string()
          .min(1, `${column.name} is required`)
          .transform((val) => {
            const lower = val.toLowerCase().trim()
            if (lower === "true" || lower === "1") return true
            if (lower === "false" || lower === "0") return false
            throw new Error(`Invalid boolean value. Use true, false, 1, or 0`)
          })
        break
      case "JSONB":
        columnValidations[column.name] = z
          .string()
          .refine(
            (val) => {
              try {
                JSON.parse(val)
                return true
              } catch (_e) {
                return false
              }
            },
            { message: `${column.name} must be valid JSON` }
          )
          .transform((val) => JSON.parse(val))
        break
      case "TIMESTAMPTZ":
        columnValidations[column.name] = z
          .string()
          .min(1, `${column.name} is required`)
          .refine(
            (val) => !Number.isNaN(new Date(val).getTime()),
            `${column.name} must be a valid date and time`
          )
        break
      case "SELECT": {
        if (options && options.length > 0) {
          columnValidations[column.name] = z
            .string()
            .refine(
              (val) => options.includes(val),
              `${column.name} must be one of the defined options`
            )
        } else {
          columnValidations[column.name] = z
            .string()
            .min(1, `${column.name} is required`)
        }
        break
      }
      case "MULTI_SELECT": {
        const optionSchema =
          options && options.length > 0
            ? z
                .string()
                .refine(
                  (val) => options.includes(val),
                  "Invalid option selected"
                )
            : z.string().min(1, `${column.name} is required`)
        columnValidations[column.name] = z
          .array(optionSchema)
          .min(1, `Select at least one value for ${column.name}`)
        break
      }
      case "DATE":
        columnValidations[column.name] = z
          .string()
          .min(1, `${column.name} is required`)
          .refine((val) => {
            try {
              return !Number.isNaN(new Date(val).getTime())
            } catch {
              return false
            }
          }, `${column.name} must be a valid date`)
        break
      default:
        // Default to text for any unknown types
        columnValidations[column.name] = z
          .string()
          .min(1, `${column.name} is required`)
    }
  })

  return z.object(columnValidations)
}

/** Values of a new row, keyed by column name, as the row form submits them. */
export type TableRowFormData = Record<string, unknown>

/** Props for {@link TableRowFormDialog}. */
export interface TableRowFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Columns to render one field each for, in order. */
  columns: readonly TableColumnRead[]
  /** Table the row goes into; names it in the default description. */
  tableName?: string | null
  /** Replaces the default description under the title. */
  description?: string
  /**
   * Persists the validated row. The dialog closes when the promise resolves
   * and stays open, values intact, when it rejects.
   */
  onSubmit: (data: TableRowFormData) => Promise<unknown>
  /** Disables the submit button while a submit is in flight. */
  isPending?: boolean
}

/**
 * The "Add new row" form: one typed field per column, validated against the
 * column types. It knows nothing about where the row is written, so the
 * tables route and the case page each wrap it with their own insert.
 */
export function TableRowFormDialog({
  open,
  onOpenChange,
  columns,
  tableName,
  description,
  onSubmit,
  isPending = false,
}: TableRowFormDialogProps) {
  const schema = useMemo(() => createInsertTableRowSchema(columns), [columns])

  const form = useForm<TableRowFormData>({
    resolver: zodResolver(schema),
    defaultValues: {},
  })

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      form.reset()
    }
    onOpenChange(nextOpen)
  }

  const handleSubmit = async (data: TableRowFormData) => {
    try {
      await onSubmit(data)
      handleOpenChange(false)
    } catch (error) {
      console.error(error)
    }
  }

  let resolvedDescription = "Add a new row to this table."
  if (description) {
    resolvedDescription = description
  } else if (tableName) {
    resolvedDescription = `Add a new row to the "${tableName}" table.`
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>Add new row</DialogTitle>
          <DialogDescription>{resolvedDescription}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              <div className="space-y-4 pr-1">
                {columns.map((column) => (
                  <FormField
                    key={column.name}
                    control={form.control}
                    name={column.name}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="flex items-center gap-2">
                          <span>{column.name}</span>
                          <SqlTypeBadge type={column.type as SqlType} />
                        </FormLabel>
                        <FormControl>
                          <DynamicInput column={column} field={field} />
                        </FormControl>
                        <FormMessage className="text-xs" />
                      </FormItem>
                    )}
                  />
                ))}
              </div>
            </div>
            <DialogFooter className="pt-4">
              <Button type="submit" disabled={isPending}>
                Add row
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The tables route's "Add new row" dialog: reads the table from the route and
 * inserts through the tables API.
 */
export function TableInsertRowDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const params = useParams<{ tableId: string }>()
  const tableId = params?.tableId
  const workspaceId = useWorkspaceId()
  const { table } = useGetTable({ tableId: tableId || "", workspaceId })
  const { insertRow, insertRowIsPending } = useInsertRow()

  if (!table) {
    return null
  }

  async function handleSubmit(data: TableRowFormData) {
    if (!tableId) {
      throw new Error("Table ID is missing")
    }
    await insertRow({
      requestBody: {
        data,
      },
      tableId,
      workspaceId,
    })
  }

  return (
    <TableRowFormDialog
      open={open}
      onOpenChange={onOpenChange}
      columns={table.columns}
      tableName={table.name}
      onSubmit={handleSubmit}
      isPending={insertRowIsPending}
    />
  )
}
