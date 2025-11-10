"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { PlusCircle, Trash2Icon } from "lucide-react"
import { useFieldArray, useForm } from "react-hook-form"
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
import { useCreateTable } from "@/lib/hooks"
import { parseEnumValuesInput, SqlTypeCreatableEnum } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

const createTableSchema = z.object({
  name: z
    .string()
    .min(1, "Name is required")
    .max(100, "Name cannot exceed 100 characters")
    .regex(
      /^[a-zA-Z0-9_]+$/,
      "Name must contain only letters, numbers, and underscores"
    ),
  columns: z
    .array(
      z
        .object({
          name: z.string().min(1, "Column name is required"),
          type: z.enum(SqlTypeCreatableEnum),
          enumValues: z.string().optional(),
        })
        .superRefine((column, ctx) => {
          if (column.type === "ENUM") {
            const parsed = parseEnumValuesInput(column.enumValues)
            if (parsed.length === 0) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Provide at least one enum value",
                path: ["enumValues"],
              })
            }
          }
        })
    )
    .min(1, "At least one column is required"),
})

type CreateTableSchema = z.infer<typeof createTableSchema>

export function CreateTableDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = useWorkspaceId()
  const { createTable, createTableIsPending } = useCreateTable()

  const form = useForm<CreateTableSchema>({
    resolver: zodResolver(createTableSchema),
    defaultValues: {
      name: "",
      columns: [{ name: "", type: SqlTypeCreatableEnum[0] }],
    },
    mode: "onSubmit",
  })
  const { fields, append, remove } = useFieldArray<CreateTableSchema>({
    control: form.control,
    name: "columns",
  })

  const onSubmit = async (data: CreateTableSchema) => {
    try {
      form.clearErrors()

      if (data.columns.length === 0) {
        form.setError("columns", {
          type: "manual",
          message: "At least one column is required",
        })
        return
      }

      const duplicates = new Set<number>()
      const seen = new Map<string, number>()
      data.columns.forEach((column, index) => {
        const normalised = column.name.trim().toLowerCase()
        if (!normalised) {
          return
        }
        const existing = seen.get(normalised)
        if (existing !== undefined) {
          duplicates.add(index)
          duplicates.add(existing)
        } else {
          seen.set(normalised, index)
        }
      })

      if (duplicates.size > 0) {
        duplicates.forEach((index) => {
          form.setError(`columns.${index}.name`, {
            type: "manual",
            message: "Column names must be unique",
          })
        })
        form.setError("root", {
          type: "manual",
          message: "Column names must be unique",
        })
        return
      }

      const columnsPayload: TableColumnCreate[] = data.columns.map(
        ({ name, type, enumValues }) => {
          const column: TableColumnCreate = {
            name: name.trim(),
            type,
            nullable: true,
          }

          if (type === "ENUM") {
            const values = parseEnumValuesInput(enumValues)
            if (values.length > 0) {
              column.default = {
                enum_values: values,
              }
            }
          }

          return column
        }
      )

      await createTable({
        requestBody: {
          name: data.name,
          columns: columnsPayload,
        },
        workspaceId,
      })
      onOpenChange(false)
      form.reset()
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 409) {
          form.setError("name", {
            type: "manual",
            message: "A table with this name already exists",
          })
        } else {
          form.setError("root", {
            type: "manual",
            message: error.message,
          })
        }
      } else {
        form.setError("root", {
          type: "manual",
          message:
            error instanceof Error ? error.message : "Failed to create table",
        })
        console.error("Error creating table:", error)
        return
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[625px]">
        <DialogHeader>
          <DialogTitle>Create new table</DialogTitle>
          <DialogDescription>
            Define your table structure by adding columns and their data types.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter table name..."
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormDescription>
                    Name of the table. Must be unique within the workspace.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="space-y-4">
              <FormField
                control={form.control}
                name="columns"
                render={() => (
                  <FormItem>
                    <FormLabel>Columns</FormLabel>
                    <FormDescription>
                      Define the columns of the table.
                    </FormDescription>
                    {fields.map((field, index) => {
                      const columnType = form.watch(`columns.${index}.type`)
                      return (
                        <div key={field.id} className="mb-4 space-y-2">
                          <div className="flex items-start">
                            <div className="grid flex-1 grid-cols-3 gap-2">
                              <FormField
                                control={form.control}
                                name={`columns.${index}.name`}
                                render={({ field }) => (
                                  <FormItem className="col-span-2">
                                    <FormControl>
                                      <Input
                                        placeholder="Enter column name..."
                                        {...field}
                                        value={field.value ?? ""}
                                      />
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )}
                              />
                              <FormField
                                control={form.control}
                                name={`columns.${index}.type`}
                                render={({ field }) => (
                                  <FormItem className="col-span-1">
                                    <FormControl>
                                      <Select
                                        value={field.value}
                                        onValueChange={field.onChange}
                                      >
                                        <SelectTrigger>
                                          <SelectValue placeholder="Select column type" />
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
                            </div>
                            <Button
                              type="button"
                              variant="ghost"
                              className="ml-2"
                              onClick={() => {
                                if (fields.length > 1) {
                                  remove(index)
                                }
                              }}
                              disabled={fields.length <= 1}
                              aria-label="Remove column"
                            >
                              <Trash2Icon className="size-3.5" />
                            </Button>
                          </div>
                          {columnType === "ENUM" && (
                            <FormField
                              control={form.control}
                              name={`columns.${index}.enumValues`}
                              render={({ field }) => {
                                const enumValues = parseEnumValuesInput(
                                  field.value
                                )
                                const currentTags = enumValues.map(
                                  (val, i) => ({
                                    id: `${i}`,
                                    text: val,
                                  })
                                )
                                return (
                                  <FormItem>
                                    <FormLabel className="sr-only">
                                      Enum values
                                    </FormLabel>
                                    <FormDescription className="text-xs text-muted-foreground">
                                      Press Enter to add each value.
                                    </FormDescription>
                                    <FormControl>
                                      <CustomTagInput
                                        tags={currentTags}
                                        setTags={(newTags) => {
                                          const resolvedTags =
                                            typeof newTags === "function"
                                              ? newTags(currentTags)
                                              : newTags
                                          field.onChange(
                                            resolvedTags
                                              .map((tag) => tag.text)
                                              .join("\n")
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
                          )}
                        </div>
                      )
                    })}
                  </FormItem>
                )}
              />
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  append({
                    name: "",
                    type: SqlTypeCreatableEnum[0],
                    enumValues: "",
                  })
                }
                className="space-x-2 text-xs"
                aria-label="Add new column"
              >
                <PlusCircle className="mr-2 size-4" />
                Add column
              </Button>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={createTableIsPending}>
                Create table
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
