"use client"

import { ApiError, TableReadMinimal } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2, PlusCircle } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useUpdateTable } from "@/lib/hooks"
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

const updateTableSchema = z.object({
  name: z
    .string()
    .min(1, "Name is required")
    .max(100, "Name cannot exceed 100 characters")
    .regex(
      /^[a-zA-Z0-9_]+$/,
      "Name must contain only letters, numbers, and underscores"
    ),
})

type UpdateTableSchema = z.infer<typeof updateTableSchema>

export function TableEditDialog({
  table,
  open,
  onOpenChange,
}: {
  table: TableReadMinimal
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { workspaceId } = useWorkspace()
  const { updateTable, updateTableIsPending } = useUpdateTable()

  const form = useForm<UpdateTableSchema>({
    resolver: zodResolver(updateTableSchema),
    defaultValues: {
      name: "",
    },
  })

  const onSubmit = async (data: UpdateTableSchema) => {
    try {
      await updateTable({
        requestBody: data,
        tableId: table.id,
        workspaceId,
      })
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error(error)
      if (error instanceof ApiError) {
        if (error.status === 409) {
          form.setError("name", {
            message: "A table with this name already exists",
          })
        } else {
          form.setError("root", { message: error.message })
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader className="space-y-4">
          <DialogTitle>Edit Table</DialogTitle>
          <DialogDescription>Edit the {table.name} table.</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="flex items-center gap-2 text-xs">
                    Name
                  </FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormDescription>The new name for the table.</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="submit" disabled={updateTableIsPending}>
                {updateTableIsPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : (
                  <PlusCircle className="mr-2 size-4" />
                )}
                Update Table
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
