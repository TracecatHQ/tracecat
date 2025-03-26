"use client"

import { useParams } from "next/navigation"
import { TableColumnRead, TableRead } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { Checkbox } from "@radix-ui/react-checkbox"
import { Loader2, PlusCircle } from "lucide-react"
import { ControllerRenderProps, useForm } from "react-hook-form"
import { z } from "zod"

import { useGetTable, useInsertRow } from "@/lib/hooks"
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
import { Input } from "@/components/ui/input"

// Update the schema to be dynamic based on table columns
const createInsertTableRowSchema = (table: TableRead) => {
  const columnValidations: Record<string, z.ZodType> = {}

  table.columns.forEach((column) => {
    // Add validation based on SQL type
    switch (column.type.toLowerCase()) {
      case "text":
      case "varchar":
      case "char":
        columnValidations[column.name] = z
          .string()
          .min(1, `${column.name} is required`)
        break
      case "integer":
      case "bigint":
      case "smallint":
      case "decimal":
      case "numeric":
      case "real":
      case "double precision":
        columnValidations[column.name] = z
          .number()
          .min(-Infinity, `${column.name} must be a number`)
        break
      case "boolean":
        columnValidations[column.name] = z.boolean()
        break
      case "date":
      case "timestamp":
        columnValidations[column.name] = z.date()
        break
      case "json":
      case "jsonb":
        columnValidations[column.name] = z
          .string()
          .refine(
            (val) => {
              try {
                JSON.parse(val)
                return true
              } catch (e) {
                return false
              }
            },
            { message: `${column.name} must be valid JSON` }
          )
          .transform((val) => JSON.parse(val))
        break
      default:
        columnValidations[column.name] = z
          .string()
          .min(1, `${column.name} is required`)
    }
  })

  return z.object(columnValidations)
}

type DynamicFormData = Record<string, string | number | boolean>

export function TableInsertRowDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { tableId } = useParams<{ tableId: string }>()
  const { workspaceId } = useWorkspace()
  const { table } = useGetTable({ tableId, workspaceId })
  const { insertRow, insertRowIsPending } = useInsertRow()

  // Create form schema once table data is available
  const schema = table ? createInsertTableRowSchema(table) : z.object({})

  const form = useForm<DynamicFormData>({
    resolver: zodResolver(schema),
    defaultValues: {},
  })

  const onSubmit = async (data: DynamicFormData) => {
    try {
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add New Row</DialogTitle>
          <DialogDescription>
            Add a new row to the {table.name} table.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            {table.columns.map((column) => (
              <FormField
                key={column.name}
                control={form.control}
                name={column.name}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="flex items-center gap-2 text-xs lowercase">
                      <span className="font-semibold">{column.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {column.type}
                      </span>
                    </FormLabel>
                    <FormControl>
                      <DynamicInput column={column} field={field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ))}
            <DialogFooter>
              <Button type="submit" disabled={insertRowIsPending}>
                {insertRowIsPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : (
                  <PlusCircle className="mr-2 size-4" />
                )}
                Insert Row
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function DynamicInput({
  column,
  field,
}: {
  column: TableColumnRead
  field: ControllerRenderProps<DynamicFormData, string>
}) {
  switch (column.type.toLowerCase()) {
    case "boolean":
      return (
        <Checkbox
          checked={field.value as boolean}
          onCheckedChange={field.onChange}
        />
      )
    case "integer":
    case "bigint":
    case "smallint":
    case "decimal":
    case "numeric":
    case "real":
    case "double precision":
      return (
        <Input
          type="number"
          value={field.value as number}
          onChange={(e) => field.onChange(Number(e.target.value))}
        />
      )
    default:
      return (
        <Input
          type="text"
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
  }
}
