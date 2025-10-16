"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { CalendarClock, Clock, PlusCircle } from "lucide-react"
import { useParams } from "next/navigation"
import { type ControllerRenderProps, useForm } from "react-hook-form"
import { format } from "date-fns"
import { z } from "zod"
import type { TableColumnRead, TableRead } from "@/client"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { Spinner } from "@/components/loading/spinner"
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
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { SqlType } from "@/lib/data-type"
import { useGetTable, useInsertRow } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

// Update the schema to be dynamic based on table columns
const createInsertTableRowSchema = (table: TableRead) => {
  const columnValidations: Record<string, z.ZodType> = {}

  table.columns.forEach((column) => {
    // Add validation based on SQL type - only handle the 5 supported types
    switch (column.type.toUpperCase()) {
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add new row</DialogTitle>
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
            <DialogFooter>
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

function DynamicInput({
  column,
  field,
}: {
  column: TableColumnRead
  field: ControllerRenderProps<DynamicFormData, string>
}) {
  switch (column.type.toUpperCase()) {
    case "BOOLEAN":
      return (
        <Input
          type="text"
          placeholder="true, false, 1, or 0"
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
    case "INTEGER":
      return (
        <Input
          type="number"
          placeholder="Enter an integer"
          value={field.value as number}
          onChange={(e) => field.onChange(Number(e.target.value))}
        />
      )
    case "NUMERIC":
      return (
        <Input
          type="number"
          step="any"
          placeholder="Enter a number"
          value={field.value as number}
          onChange={(e) => field.onChange(Number(e.target.value))}
        />
      )
    case "JSONB":
      return (
        <Input
          type="text"
          placeholder='{"key": "value"}'
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
    case "TIMESTAMPTZ":
      return <DateTimePickerField field={field} />
    case "TEXT":
    default:
      return (
        <Input
          type="text"
          placeholder="Enter text"
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
  }
}

function DateTimePickerField({
  field,
}: {
  field: ControllerRenderProps<DynamicFormData, string>
}) {
  const [open, setOpen] = React.useState(false)
  const stringValue =
    typeof field.value === "string" && field.value.length > 0
      ? field.value
      : ""
  const dateValue = stringValue ? new Date(stringValue) : undefined

  const handleSelect = React.useCallback(
    (date: Date | undefined) => {
      if (!date) {
        field.onChange("")
        return
      }

      const next = new Date(date)
      const hours = dateValue?.getHours() ?? 0
      const minutes = dateValue?.getMinutes() ?? 0
      next.setHours(hours, minutes, 0, 0)
      field.onChange(next.toISOString())
    },
    [dateValue, field]
  )

  const handleTimeChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      if (!dateValue) return

      const [hoursStr = "", minutesStr = ""] = event.target.value.split(":")
      const hours = Number.parseInt(hoursStr, 10)
      const minutes = Number.parseInt(minutesStr, 10)
      if (Number.isNaN(hours) || Number.isNaN(minutes)) return

      const next = new Date(dateValue)
      next.setHours(hours, minutes, 0, 0)
      field.onChange(next.toISOString())
    },
    [dateValue, field]
  )

  const handleSetNow = React.useCallback(() => {
    const now = new Date()
    field.onChange(now.toISOString())
    setOpen(false)
  }, [field, setOpen])

  const handleClear = React.useCallback(() => {
    field.onChange("")
    setOpen(false)
  }, [field, setOpen])

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen)
        if (!nextOpen) {
          field.onBlur()
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className={cn(
            "w-full justify-start text-left font-normal text-sm",
            !dateValue && "text-xs text-muted-foreground"
          )}
        >
          <CalendarClock className="mr-2 size-4" />
          {dateValue ? format(dateValue, "PPP HH:mm") : "Select date and time"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={dateValue}
          onSelect={handleSelect}
          initialFocus
        />
        <div className="flex flex-col gap-2 border-t border-border p-3">
          <Input
            type="time"
            value={dateValue ? format(dateValue, "HH:mm") : ""}
            onChange={handleTimeChange}
            step={60}
            disabled={!dateValue}
          />
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              className="flex-1 text-xs"
              onClick={handleSetNow}
            >
              <Clock className="mr-2 size-4" />
              Now
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="flex-1 text-xs text-muted-foreground"
              onClick={handleClear}
              disabled={!stringValue}
            >
              Clear
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
