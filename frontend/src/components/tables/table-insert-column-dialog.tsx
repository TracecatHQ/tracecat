"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useParams } from "next/navigation"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { TableColumnCreate } from "@/client"
import { ApiError } from "@/client"
import { SqlTypeDisplay } from "@/components/data-type/sql-type-display"
import { MultiTagCommandInput } from "@/components/tags-input"
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
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { TracecatApiError } from "@/lib/errors"
import { useGetTable, useInsertColumn } from "@/lib/hooks"
import { SqlTypeCreatableEnum } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

const isSelectableColumnType = (type?: string) =>
  type === "SELECT" || type === "MULTI_SELECT"

const sanitizeColumnOptions = (options?: string[]) => {
  if (!options) return []
  const seen = new Set<string>()
  const cleaned: string[] = []
  for (const option of options) {
    const trimmed = option.trim()
    if (trimmed.length === 0 || seen.has(trimmed)) {
      continue
    }
    seen.add(trimmed)
    cleaned.push(trimmed)
  }
  return cleaned
}

// Update schema for column creation
const createInsertTableColumnSchema = z
  .object({
    name: z.string().min(1, "Column name is required"),
    type: z.enum(SqlTypeCreatableEnum),
    options: z.array(z.string().min(1, "Option cannot be empty")).optional(),
  })
  .superRefine((column, ctx) => {
    if (isSelectableColumnType(column.type)) {
      const hasValidOptions = column.options && column.options.length > 0
      if (!hasValidOptions) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Please add at least one option",
          path: ["options"],
        })
      }
    }
  })

type ColumnFormData = z.infer<typeof createInsertTableColumnSchema>

export function TableInsertColumnDialog({
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
  const { insertColumn, insertColumnIsPending } = useInsertColumn()

  const form = useForm<ColumnFormData>({
    resolver: zodResolver(createInsertTableColumnSchema),
    defaultValues: {
      name: "",
      options: [],
    },
  })
  const selectedType = form.watch("type")
  const requiresOptions = isSelectableColumnType(selectedType)

  const onSubmit = async (data: ColumnFormData) => {
    try {
      if (!tableId) {
        console.error("Table ID is missing")
        return
      }
      const payload: TableColumnCreate = {
        name: data.name,
        type: data.type,
      }

      if (isSelectableColumnType(data.type)) {
        payload.options = sanitizeColumnOptions(data.options)
      }

      await insertColumn({
        requestBody: payload,
        tableId,
        workspaceId,
      })
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error(error)
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError
        if (apiError.status === 422) {
          form.setError("type", {
            message: JSON.stringify(apiError.body.detail, null, 2),
          })
        } else if (apiError.status === 409) {
          form.setError("name", {
            message: "A column with this name already exists",
          })
        } else {
          form.setError("root", { message: error.message })
        }
      }
    }
  }

  if (!table) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add new column</DialogTitle>
          <DialogDescription>
            Add a new column to the {table.name} table.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Column name</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="flex items-center gap-2">
                    <span>Data type</span>
                    <span className="text-xs text-muted-foreground">
                      (required)
                    </span>
                  </FormLabel>
                  <FormControl>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a type..." />
                      </SelectTrigger>
                      <SelectContent>
                        {SqlTypeCreatableEnum.map((type) => (
                          <SelectItem key={type} value={type}>
                            <SqlTypeDisplay
                              type={type}
                              labelClassName="text-xs"
                            />
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {requiresOptions && (
              <FormField
                control={form.control}
                name="options"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Options</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={field.value || []}
                        onChange={field.onChange}
                        placeholder="Add allowed values..."
                        allowCustomTags
                        disableSuggestions
                        className="w-full"
                        searchKeys={["label"]}
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      Define the values users can choose from when inserting
                      rows.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}
            <DialogFooter>
              <Button type="submit" disabled={insertColumnIsPending}>
                Add column
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
