"use client"

import { useParams } from "next/navigation"
import { ApiError } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { PlusCircle } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { TracecatApiError } from "@/lib/errors"
import { useGetTable, useInsertColumn } from "@/lib/hooks"
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
import { Spinner } from "@/components/loading/spinner"

// Update schema for column creation
const createInsertTableColumnSchema = z.object({
  name: z.string().min(1, "Column name is required"),
  type: z.enum(SqlTypeEnum),
})

type ColumnFormData = z.infer<typeof createInsertTableColumnSchema>

export function TableInsertColumnDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { tableId } = useParams<{ tableId: string }>()
  const { workspaceId } = useWorkspace()
  const { table } = useGetTable({ tableId, workspaceId })
  const { insertColumn, insertColumnIsPending } = useInsertColumn()

  const form = useForm<ColumnFormData>({
    resolver: zodResolver(createInsertTableColumnSchema),
    defaultValues: {
      name: "",
    },
  })

  const onSubmit = async (data: ColumnFormData) => {
    try {
      await insertColumn({
        requestBody: data,
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
          <DialogTitle>Add Column</DialogTitle>
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
                  <FormLabel>Column Name</FormLabel>
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
                  <FormLabel className="flex items-center gap-2 text-xs">
                    <span>Data Type</span>
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
                        {SqlTypeEnum.map((type) => (
                          <SelectItem key={type} value={type}>
                            {type}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="submit" disabled={insertColumnIsPending}>
                {insertColumnIsPending ? (
                  <Spinner className="mr-2 size-4" />
                ) : (
                  <PlusCircle className="mr-2 size-4" />
                )}
                Add Column
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
