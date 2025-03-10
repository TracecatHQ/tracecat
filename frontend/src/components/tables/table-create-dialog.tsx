"use client"

import { ApiError } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { PlusCircle, Trash2Icon } from "lucide-react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"

import { useCreateTable } from "@/lib/hooks"
import { SqlTypeEnum } from "@/lib/tables"
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

const createTableSchema = z.object({
  name: z
    .string()
    .min(1, "Name is required")
    .max(100, "Name cannot exceed 100 characters")
    .regex(
      /^[a-zA-Z0-9_]+$/,
      "Name must contain only letters, numbers, and underscores"
    ),
  columns: z.array(
    z.object({
      name: z.string().min(1, "Column name is required"),
      type: z.enum(SqlTypeEnum),
    })
  ),
})

type CreateTableSchema = z.infer<typeof createTableSchema>

interface SelectItem {
  label: string
  value: string
}

export function CreateTableDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { workspaceId } = useWorkspace()
  const { createTable, createTableIsPending } = useCreateTable()

  const form = useForm<CreateTableSchema>({
    resolver: zodResolver(createTableSchema),
    defaultValues: {
      name: "",
      columns: [],
    },
  })
  const { fields, append, remove } = useFieldArray<CreateTableSchema>({
    control: form.control,
    name: "columns",
  })

  const onSubmit = async (data: CreateTableSchema) => {
    try {
      await createTable({
        requestBody: {
          name: data.name,
          columns: data.columns,
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
                    {fields.map((field, index) => (
                      <div key={field.id} className="flex items-center">
                        <div className="grid flex-1 grid-cols-3 gap-2">
                          <FormField
                            control={form.control}
                            name={`columns.${index}.name`}
                            render={({ field }) => (
                              <FormControl className="col-span-2">
                                <Input
                                  placeholder="Enter column name..."
                                  {...field}
                                  value={field.value ?? ""}
                                />
                              </FormControl>
                            )}
                          />
                          <FormField
                            control={form.control}
                            name={`columns.${index}.type`}
                            render={({ field }) => (
                              <FormControl className="col-span-1">
                                <Select
                                  value={field.value}
                                  onValueChange={field.onChange}
                                >
                                  <SelectTrigger>
                                    <SelectValue
                                      placeholder="Select column type"
                                      className="w-full"
                                    />
                                  </SelectTrigger>
                                  <SelectContent>
                                    {SqlTypeEnum.map((type) => (
                                      <SelectItem key={type} value={type}>
                                        {type}
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              </FormControl>
                            )}
                          />
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          className="ml-2"
                          onClick={() => remove(index)}
                          aria-label="Remove column"
                        >
                          <Trash2Icon className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => append({ name: "", type: SqlTypeEnum[0] })}
                className="space-x-2 text-xs"
                aria-label="Add new column"
              >
                <PlusCircle className="mr-2 size-4" />
                Add Column
              </Button>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={createTableIsPending}>
                Create Table
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
