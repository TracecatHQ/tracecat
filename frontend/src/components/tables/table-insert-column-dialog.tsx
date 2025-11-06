"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useParams } from "next/navigation"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { TableColumnCreate } from "@/client"
import { ApiError } from "@/client"
import { SqlTypeDisplay } from "@/components/data-type/sql-type-display"
import { CustomTagInput } from "@/components/tags-input"
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
import { parseEnumValuesInput, SqlTypeCreatableEnum } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

// Update schema for column creation
const createInsertTableColumnSchema = z
  .object({
    name: z.string().min(1, "Column name is required"),
    type: z.enum(SqlTypeCreatableEnum),
    enumValues: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.type === "ENUM") {
      const values = parseEnumValuesInput(data.enumValues)
      if (values.length === 0) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Provide at least one enum value",
          path: ["enumValues"],
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
      enumValues: "",
    },
  })

  const typeValue = form.watch("type")

  const onSubmit = async (data: ColumnFormData) => {
    try {
      if (!tableId) {
        console.error("Table ID is missing")
        return
      }

      const sanitizedName = data.name.trim().toLowerCase()
      if (
        table?.columns.some(
          (column) => column.name.toLowerCase() === sanitizedName
        )
      ) {
        form.setError("name", {
          type: "manual",
          message: "A column with this name already exists",
        })
        return
      }

      const payload: TableColumnCreate = {
        name: sanitizedName,
        type: data.type,
        nullable: true,
      }

      if (data.type === "ENUM") {
        const values = parseEnumValuesInput(data.enumValues)
        if (values.length > 0) {
          payload.default = {
            enum_values: values,
          }
        }
      }

      await insertColumn({
        requestBody: payload,
        tableId,
        workspaceId,
      })
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Error inserting column:", error)
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError
        if (apiError.status === 409) {
          // Handle unique constraint violation
          const errorMessage =
            typeof apiError.body.detail === "string"
              ? apiError.body.detail
              : "A column with this name already exists"
          form.setError("name", {
            type: "manual",
            message: errorMessage,
          })
        } else if (apiError.status === 422) {
          // Handle validation errors
          const detail = apiError.body.detail
          if (Array.isArray(detail)) {
            // Pydantic validation errors
            detail.forEach((err: { loc?: string[]; msg?: string }) => {
              if (err.loc && err.loc.length > 0) {
                const fieldName = err.loc[
                  err.loc.length - 1
                ] as keyof ColumnFormData
                form.setError(fieldName, {
                  type: "manual",
                  message: err.msg || "Validation error",
                })
              }
            })
          } else if (typeof detail === "string") {
            form.setError("type", {
              type: "manual",
              message: detail,
            })
          } else {
            form.setError("type", {
              type: "manual",
              message: JSON.stringify(detail, null, 2),
            })
          }
        } else {
          form.setError("root", {
            type: "manual",
            message:
              typeof apiError.body.detail === "string"
                ? apiError.body.detail
                : apiError.message || "Failed to insert column",
          })
        }
      } else {
        form.setError("root", {
          type: "manual",
          message:
            error instanceof Error ? error.message : "Failed to insert column",
        })
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
            {typeValue === "ENUM" ? (
              <FormField
                control={form.control}
                name="enumValues"
                render={({ field }) => {
                  const enumValues = parseEnumValuesInput(field.value)
                  return (
                    <FormItem>
                      <FormLabel>Enum values</FormLabel>
                      <FormDescription>
                        Press Enter to add each value.
                      </FormDescription>
                      <FormControl>
                        <CustomTagInput
                          tags={enumValues.map((val, i) => ({
                            id: `${i}`,
                            text: val,
                          }))}
                          setTags={(newTags) => {
                            const resolvedTags =
                              typeof newTags === "function"
                                ? newTags([])
                                : newTags
                            field.onChange(
                              resolvedTags.map((tag) => tag.text).join("\n")
                            )
                          }}
                          placeholder="Add enum values..."
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )
                }}
              />
            ) : null}
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
