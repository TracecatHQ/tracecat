"use client"

import { useState } from "react"
import { useParams } from "next/navigation"
import { ApiError, TableColumnRead } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  ChevronDownIcon,
  CopyIcon,
  KeyIcon,
  Loader2,
  Pencil,
  Trash2Icon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { userIsPrivileged } from "@/lib/auth"
import { useDeleteColumn, useUpdateColumn } from "@/lib/hooks"
import { SqlTypeEnum } from "@/lib/tables"
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

type TableViewColumnMenuType = "delete" | "edit" | "set-natural-key" | null

export function TableViewColumnMenu({ column }: { column: TableColumnRead }) {
  const { user } = useAuth()
  const { tableId } = useParams<{ tableId?: string }>()
  const [activeType, setActiveType] = useState<TableViewColumnMenuType>(null)
  const onOpenChange = () => setActiveType(null)
  const isPrivileged = userIsPrivileged(user)

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
          {isPrivileged && (
            <>
              <DropdownMenuItem
                className="py-1 text-xs text-foreground/80"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("set-natural-key")
                }}
                disabled={column.is_index}
              >
                <KeyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                {column.is_index ? "Natural Key" : "Make natural key"}
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
      type: column.type,
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
                  <Loader2 className="mr-2 size-4 animate-spin" />
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
            <AlertDialogTitle>Column is already a Natural Key</AlertDialogTitle>
            <AlertDialogDescription>
              Column <b>{column.name}</b> is already a natural key with a unique
              index.
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
        title: "Natural key created",
        description: "Column is now a natural key.",
      })

      onOpenChange()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast({
          title: "Error creating natural key",
          description:
            "Column contains duplicate values. All values must be unique.",
          variant: "destructive",
        })
      } else {
        toast({
          title: "Error creating natural key",
          description: "An unexpected error occurred",
          variant: "destructive",
        })
      }
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
          <AlertDialogTitle>Create Natural Key</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to make column <b>{column.name}</b> a natural
            key? This will create a unique index on the column, making it usable
            for upsert operations.
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
                <Loader2 className="mr-2 size-4 animate-spin" />
                Creating...
              </>
            ) : (
              <>
                <KeyIcon className="mr-2 size-4" />
                Create Natural Key
              </>
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
