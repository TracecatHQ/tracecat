"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { CopyIcon, PencilIcon, Tag, Trash2Icon } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import z from "zod"
import type { CaseTagRead, TagUpdate } from "@/client"
import { ColorPicker } from "@/components/color-picker"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
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

const updateTagSchema = z.object({
  name: z
    .string()
    .min(1, "Tag name cannot be empty")
    .max(50, "Tag name cannot be longer than 50 characters")
    .trim()
    .refine(
      (name) => name.trim().length > 0,
      "Tag name cannot be only whitespace"
    )
    .optional(),
  color: z.preprocess(
    (value) => {
      if (value === "" || value === null || value === undefined) {
        return undefined
      }
      return value
    },
    z
      .string()
      .regex(/^#[0-9A-Fa-f]{6}$/, "Color must be a valid hex color code")
      .optional()
  ),
})

const DEFAULT_TAG_COLOR = "#aabbcc"

enum TagItemAction {
  Edit = "edit",
  Delete = "delete",
}

interface TagsTableProps {
  tags: CaseTagRead[]
  onDeleteTag: (tagId: string) => Promise<void>
  onUpdateTag: (tagId: string, params: TagUpdate) => Promise<void>
  isDeleting?: boolean
  isUpdating?: boolean
}

export function TagsTable({
  tags,
  onDeleteTag,
  onUpdateTag,
  isDeleting,
  isUpdating,
}: TagsTableProps) {
  const [selectedTag, setSelectedTag] = useState<CaseTagRead | null>(null)
  const [action, setAction] = useState<TagItemAction | null>(null)

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedTag(null)
          setAction(null)
        }
      }}
    >
      <DataTable
        data={tags}
        columns={[
          {
            accessorKey: "name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Tag name"
              />
            ),
            cell: ({ row }) => {
              const tag = row.original
              return (
                <div className="flex items-center gap-2">
                  <Tag
                    className="size-4"
                    style={{
                      fill: "transparent",
                      stroke: tag.color ?? DEFAULT_TAG_COLOR,
                    }}
                  />
                  <span className="text-xs text-foreground/80">{tag.name}</span>
                </div>
              )
            },
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "color",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Color"
              />
            ),
            cell: ({ row }) => (
              <div className="flex items-center gap-2">
                <div
                  className="size-4 rounded border"
                  style={{
                    backgroundColor: row.original.color ?? DEFAULT_TAG_COLOR,
                    borderColor: row.original.color ?? DEFAULT_TAG_COLOR,
                  }}
                />
                <span className="font-mono text-xs text-foreground/80">
                  {row.original.color ?? DEFAULT_TAG_COLOR}
                </span>
              </div>
            ),
            enableSorting: false,
            enableHiding: false,
          },
          {
            accessorKey: "ref",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Reference"
              />
            ),
            cell: ({ row }) => (
              <span className="font-mono text-xs text-muted-foreground">
                {row.original.ref}
              </span>
            ),
            enableSorting: false,
            enableHiding: false,
          },
          {
            id: "actions",
            enableHiding: false,
            cell: ({ row }) => {
              return (
                <div className="flex justify-end">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onClick={() =>
                          navigator.clipboard.writeText(row.original.ref)
                        }
                      >
                        <CopyIcon className="mr-2 size-3" />
                        Copy reference
                      </DropdownMenuItem>
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          onClick={() => {
                            setSelectedTag(row.original)
                            setAction(TagItemAction.Edit)
                          }}
                        >
                          <PencilIcon className="mr-2 size-3" />
                          Edit tag
                        </DropdownMenuItem>
                      </AlertDialogTrigger>
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          onClick={() => {
                            setSelectedTag(row.original)
                            setAction(TagItemAction.Delete)
                          }}
                        >
                          <Trash2Icon className="mr-2 size-3" />
                          Delete tag
                        </DropdownMenuItem>
                      </AlertDialogTrigger>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              )
            },
          },
        ]}
        toolbarProps={defaultToolbarProps}
      />
      {action === TagItemAction.Delete && selectedTag && (
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete tag</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the tag{" "}
              <strong>{selectedTag.name}</strong>? This action cannot be undone
              and will remove this tag from all cases.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                if (selectedTag) {
                  try {
                    await onDeleteTag(selectedTag.id)
                    setSelectedTag(null)
                    setAction(null)
                  } catch (error) {
                    console.error("Failed to delete tag:", error)
                  }
                }
              }}
              disabled={isDeleting}
            >
              <Trash2Icon className="mr-2 size-4" />
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      )}
      {action === TagItemAction.Edit && selectedTag && (
        <EditTagDialogContent
          tag={selectedTag}
          onUpdate={onUpdateTag}
          isUpdating={isUpdating}
          onClose={() => {
            setSelectedTag(null)
            setAction(null)
          }}
        />
      )}
    </AlertDialog>
  )
}

function EditTagDialogContent({
  tag,
  onUpdate,
  isUpdating,
  onClose,
}: {
  tag: CaseTagRead
  onUpdate: (tagId: string, params: TagUpdate) => Promise<void>
  isUpdating?: boolean
  onClose: () => void
}) {
  const methods = useForm<TagUpdate>({
    resolver: zodResolver(updateTagSchema),
    defaultValues: {
      name: tag.name,
      color: tag.color ?? "",
    },
  })

  useEffect(() => {
    methods.reset({
      name: tag.name,
      color: tag.color ?? "",
    })
  }, [methods, tag.color, tag.name])

  const handleEdit = async (params: TagUpdate) => {
    try {
      await onUpdate(tag.id, {
        name: params.name || undefined,
        color: params.color || undefined,
      })
      onClose()
    } catch (error) {
      console.error("Error updating case tag", error)
      methods.setError("name", {
        type: "manual",
        message: `Error updating tag: ${String(error)}`,
      })
    }
  }

  return (
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>Edit tag</AlertDialogTitle>
      </AlertDialogHeader>
      <AlertDialogDescription>
        Edit the name and color of the tag.
      </AlertDialogDescription>
      <Form {...methods}>
        <form onSubmit={methods.handleSubmit(handleEdit)}>
          <div className="space-y-4">
            <FormField
              key="name"
              control={methods.control}
              name="name"
              defaultValue={tag.name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-sm">Name</FormLabel>
                  <FormControl>
                    <Input
                      className="text-sm"
                      placeholder="Name"
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormDescription>
                    Max 50 alphanumeric characters
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              key="color"
              control={methods.control}
              name="color"
              defaultValue={tag.color ?? ""}
              render={({ field }) => {
                const displayColor =
                  field.value && field.value.length > 0
                    ? field.value
                    : "#000000"
                return (
                  <FormItem>
                    <FormLabel className="text-sm">Color</FormLabel>
                    <div className="flex gap-2">
                      <FormControl>
                        <Input
                          className="max-w-24 font-mono text-sm"
                          placeholder="#000000"
                          {...field}
                          value={field.value ?? ""}
                        />
                      </FormControl>
                      <ColorPicker
                        value={displayColor}
                        onChange={(color) => field.onChange(color)}
                      />
                    </div>
                    <FormMessage />
                  </FormItem>
                )
              }}
            />
            <AlertDialogFooter>
              <AlertDialogCancel disabled={isUpdating}>
                Cancel
              </AlertDialogCancel>
              <Button
                type="submit"
                className="flex items-center gap-2"
                disabled={isUpdating}
              >
                <PencilIcon className="size-4" />
                Save changes
              </Button>
            </AlertDialogFooter>
          </div>
        </form>
      </Form>
    </AlertDialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<CaseTagRead> = {
  filterProps: {
    placeholder: "Filter tags by name...",
    column: "name",
  },
}
