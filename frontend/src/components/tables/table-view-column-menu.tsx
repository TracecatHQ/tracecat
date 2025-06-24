"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  ChevronDownIcon,
  CopyIcon,
  DatabaseZapIcon,
  Pencil,
  Trash2Icon,
} from "lucide-react"
import { useParams } from "next/navigation"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { ApiError, type TableColumnRead } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { toast } from "@/components/ui/use-toast"
import { useDeleteColumn, useUpdateColumn } from "@/lib/hooks"
import { SqlTypeEnum } from "@/lib/tables"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"

type TableViewColumnMenuType = "delete" | "edit" | "set-natural-key" | null

export function TableViewColumnMenu({ column }: { column: TableColumnRead }) {
  const { user } = useAuth()
  const { tableId } = useParams<{ tableId?: string }>()
  const [activeType, setActiveType] = useState<TableViewColumnMenuType>(null)
  const onOpenChange = () => setActiveType(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="size-4 p-0 !ring-0">
            <span className="sr-only">Edit column</span>
            <ChevronDownIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(column.id))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy ID
          </DropdownMenuItem>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(column.name))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy name
          </DropdownMenuItem>
          {user?.isPrivileged() && (
            <>
              <DropdownMenuItem
                className="py-1 text-xs text-foreground/80"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("set-natural-key")
                }}
                disabled={column.is_index}
              >
                <DatabaseZapIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                {column.is_index ? "Unique index" : "Create unique index"}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="py-1 text-xs text-foreground/80"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("edit")
                }}
              >
                <Pencil className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                Edit
              </DropdownMenuItem>
              <DropdownMenuItem
                className="py-1 text-xs text-destructive"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("delete")
                }}
              >
                <Trash2Icon className="mr-2 size-3 group-hover/item:text-destructive" />
                Delete
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <TableColumnDeleteDialog
        tableId={tableId}
        column={column}
        open={activeType === "delete"}
        onOpenChange={onOpenChange}
      />
      <TableColumnEditDialog
        tableId={tableId}
        column={column}
        open={activeType === "edit"}
        onOpenChange={onOpenChange}
      />
      <TableColumnIndexDialog
        tableId={tableId}
        column={column}
        open={activeType === "set-natural-key"}
        onOpenChange={onOpenChange}
      />
    </>
  )
}

function TableColumnDeleteDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: () => void
}) {
  const { workspaceId } = useWorkspace()
  const { deleteColumn } = useDeleteColumn()
  const [confirmName, setConfirmName] = useState("")

  if (!tableId || !workspaceId) {
    return null
  }

  const handleDeleteColumn = async () => {
    if (confirmName !== column.name) {
      toast({
        title: "Column name does not match",
        description: "Please type the exact column name to confirm deletion",
      })
      return
    }

    try {
      await deleteColumn({
        tableId,
        workspaceId,
        columnId: column.id,
      })
      onOpenChange()
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete Column</AlertDialogTitle>
          <AlertDialogDescription>
            To confirm deletion, type the column name <b>{column.name}</b>{" "}
            below. This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="my-4">
          <Input
            placeholder={`Type "${column.name}" to confirm`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDeleteColumn}
            variant="destructive"
            disabled={confirmName !== column.name}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const updateColumnSchema = z.object({
  name: z
    .string()
    .min(1, { message: "Name must be at least 1 character" })
    .max(255, { message: "Name must be less than 255 characters" })
    .regex(/^[a-zA-Z0-9_]+$/, {
      message: "Name must contain only letters, numbers, and underscores",
    }),
  type: z.enum(SqlTypeEnum),
  nullable: z.boolean(),
})

type UpdateColumnSchema = z.infer<typeof updateColumnSchema>

function TableColumnEditDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { workspaceId } = useWorkspace()
  const { updateColumn, updateColumnIsPending } = useUpdateColumn()

  const form = useForm<UpdateColumnSchema>({
    resolver: zodResolver(updateColumnSchema),
    defaultValues: {
      name: column.name,
      type: column.type as (typeof SqlTypeEnum)[number],
      nullable: column.nullable,
    },
  })

  if (!tableId || !workspaceId) {
    return null
  }

  const onSubmit = async (data: UpdateColumnSchema) => {
    try {
      const updates: Partial<UpdateColumnSchema> = {}
      if (data.name !== column.name) {
        updates.name = data.name
      }
      if (data.type !== column.type) {
        updates.type = data.type
      }
      if (data.nullable !== column.nullable) {
        updates.nullable = data.nullable
      }

      await updateColumn({
        requestBody: updates,
        tableId,
        columnId: column.id,
        workspaceId,
      })
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error(error)
      if (error instanceof ApiError) {
        if (error.status === 409) {
          form.setError("name", {
            message: "A column with this name already exists",
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
          <DialogTitle>Edit Column</DialogTitle>
          <DialogDescription>Edit the {column.name} column.</DialogDescription>
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
                  <FormDescription>
                    The new name for the column.
                  </FormDescription>
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
                    Type
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
                  <FormDescription>
                    The data type for this column.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="nullable"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center gap-2">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                        disabled
                      />
                    </FormControl>
                    <FormLabel className="text-xs">Allow null values</FormLabel>
                  </div>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="submit" disabled={updateColumnIsPending}>
                {updateColumnIsPending ? (
                  <Spinner />
                ) : (
                  <Pencil className="mr-2 size-4" />
                )}
                Update Column
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function TableColumnIndexDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: () => void
}) {
  const { workspaceId } = useWorkspace()
  const { updateColumn, updateColumnIsPending } = useUpdateColumn()

  if (!tableId || !workspaceId) {
    return null
  }

  if (column.is_index) {
    return (
      <AlertDialog open={open} onOpenChange={onOpenChange}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Column is already a unique index
            </AlertDialogTitle>
            <AlertDialogDescription>
              Column <b>{column.name}</b> is already a unique index.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Close</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    )
  }

  const handleSetIndex = async () => {
    try {
      const updates = {
        is_index: true,
      }

      await updateColumn({
        tableId,
        columnId: column.id,
        workspaceId,
        requestBody: updates,
      })

      toast({
        title: "Created unique index",
        description: "Column is now a unique index.",
      })

      onOpenChange()
    } catch (error) {
      console.error("Error creating unique index:", error)
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={() => {
        if (!updateColumnIsPending) {
          onOpenChange()
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Create unique index</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to make column <b>{column.name}</b> a unique
            index? This enables upsert operations on the table.
            <br />
            <br />
            <strong>Requirements:</strong>
            <ul className="mt-2 list-disc pl-5 text-xs">
              <li>All values in the column must be unique</li>
              <li>This cannot be undone except by recreating the column</li>
            </ul>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={updateColumnIsPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleSetIndex}
            disabled={updateColumnIsPending}
          >
            {updateColumnIsPending ? (
              <>
                <Spinner />
                Creating...
              </>
            ) : (
              <>
                <DatabaseZapIcon className="mr-2 size-4" />
                Create unique index
              </>
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
