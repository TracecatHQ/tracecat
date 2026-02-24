"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useParams } from "next/navigation"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { TableRead } from "@/client"
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
const createInsertTableRowSchema = (table: TableRead) => {
  const columnValidations: Record<string, z.ZodType> = {}

  table.columns.forEach((column) => {
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
          .number()
          .int(`${column.name} must be an integer`)
        break
      case "NUMERIC":
        columnValidations[column.name] = z
          .number()
          .min(-Infinity, `${column.name} must be a number`)
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

type DynamicFormData = Record<string, unknown>

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

  // Create form schema once table data is available
  const schema = table ? createInsertTableRowSchema(table) : z.object({})

  const form = useForm<DynamicFormData>({
    resolver: zodResolver(schema),
    defaultValues: {},
  })

  const onSubmit = async (data: DynamicFormData) => {
    try {
      if (!tableId) {
        console.error("Table ID is missing")
        return
      }
      await insertRow({
        requestBody: {
          data,
        },
        tableId,
        workspaceId,
      })
      onOpenChange(false)
      form.reset()
    } catch (error) {
      console.error(error)
    }
  }

  if (!table) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>Add new row</DialogTitle>
          <DialogDescription>
            Add a new row to the "{table.name}" table.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              <div className="space-y-4 pr-1">
                {table.columns.map((column) => (
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
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                ))}
              </div>
            </div>
            <DialogFooter className="pt-4">
              <Button type="submit" disabled={insertRowIsPending}>
                Add row
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
