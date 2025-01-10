"use client"

import { useCallback, useState } from "react"
import { useRouter } from "next/navigation"
import { TagCreate, TagRead, TagUpdate } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { EllipsisIcon, PencilIcon, Plus, Tag, Trash2Icon } from "lucide-react"
import { useForm } from "react-hook-form"
import z from "zod"

import { useTags } from "@/lib/hooks"
import { cn } from "@/lib/utils"
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
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
import { ColorPicker } from "@/components/color-picker"

const createTagSchema = z.object({
  name: z
    .string()
    .min(1, "Tag name cannot be empty")
    .max(50, "Tag name cannot be longer than 50 characters")
    .trim()
    .refine(
      (name) => name.trim().length > 0,
      "Tag name cannot be only whitespace"
    ),
  color: z
    .string()
    .regex(/^#[0-9A-Fa-f]{6}$/, "Color must be a valid hex color code"),
})

export function WorkflowTagsSidebar({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const { tags, createTag, tagsIsLoading } = useTags(workspaceId)
  const [showTagDialog, setShowTagDialog] = useState(false)

  const methods = useForm<TagCreate>({
    resolver: zodResolver(createTagSchema),
    defaultValues: {
      name: "",
      color: "#aabbcc",
    },
  })

  const handleCreateTag = async (params: TagCreate) => {
    try {
      const tagExists = tags?.some(
        (tag) => tag.name.toLowerCase() === params.name.trim().toLowerCase()
      )

      if (tagExists) {
        methods.setError("name", {
          type: "manual",
          message: "A tag with this name already exists",
        })
        return
      }

      await createTag({
        workspaceId: workspaceId,
        requestBody: params,
      })
      methods.reset()
      setShowTagDialog(false)
    } catch (error) {
      console.error("Error creating tag", error)
    }
  }

  const handleSelectTag = ({ name }: TagRead) => {
    // Set the url query param
    const searchParams = new URLSearchParams(window.location.search)
    searchParams.set("tag", name)
    router.push(`?${searchParams.toString()}`)
  }

  if (tagsIsLoading) return null

  return (
    <Dialog open={showTagDialog} onOpenChange={setShowTagDialog}>
      <div className="shrink-0 overflow-auto rounded-lg text-muted-foreground">
        <div className="sticky top-0 flex items-center justify-between bg-background p-2">
          <h2 className="text-xs font-semibold text-muted-foreground">Tags</h2>
          <div>
            <DialogTrigger asChild>
              <Button variant="ghost" size="icon" className="size-7">
                <Plus className="size-4" />
                <span className="sr-only">Add tag</span>
              </Button>
            </DialogTrigger>
          </div>
        </div>
        <div className="h-full space-y-1 p-2">
          {tags && tags.length > 0 ? (
            tags.map((tag) => (
              <TagItem
                key={tag.id}
                tag={tag}
                onSelect={() => handleSelectTag(tag)}
              />
            ))
          ) : (
            <div className="flex aspect-square h-full items-center justify-center rounded-lg border border-dashed border-muted-foreground/25 bg-muted-foreground/5 p-8">
              <div className="text-center text-xs text-muted-foreground/60">
                Use tags to organize your workflows
              </div>
            </div>
          )}
        </div>
      </div>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create New Tag</DialogTitle>
          <DialogDescription>
            Enter a name for your new workflow tag.
          </DialogDescription>
        </DialogHeader>
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(handleCreateTag)}>
            <div className="space-y-4">
              <FormField
                key="name"
                control={methods.control}
                name="name"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Name</FormLabel>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Name"
                        {...methods.register("name")}
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
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm">Color</FormLabel>
                    <div className="flex gap-2">
                      <FormControl>
                        <Input
                          className="max-w-24 font-mono text-sm"
                          placeholder="#000000"
                          value={field.value ?? "#000000"}
                          onChange={(e) => field.onChange(e.target.value)}
                        />
                      </FormControl>
                      <ColorPicker
                        value={field.value ?? "#000000"}
                        onChange={(color) => field.onChange(color)}
                      />
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button className="ml-auto space-x-2" type="submit">
                  Create Tag
                </Button>
              </DialogFooter>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

enum TagItemAction {
  Edit = "edit",
  Delete = "delete",
}

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
  color: z
    .string()
    .regex(/^#[0-9A-Fa-f]{6}$/, "Color must be a valid hex color code")
    .optional(),
})

function TagItemActionDialogContent({
  action,
  tag,
}: {
  action: TagItemAction | null
  tag: TagRead
}) {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { updateTag, deleteTag } = useTags(workspaceId)
  const methods = useForm<TagUpdate>({
    resolver: zodResolver(updateTagSchema),
    defaultValues: {
      name: tag.name,
      color: tag.color || "#aabbcc",
    },
  })

  const onEdit = useCallback(
    async (params: TagUpdate) => {
      try {
        await updateTag({
          tagId: tag.id,
          workspaceId: workspaceId,
          requestBody: {
            name: params.name || undefined,
            color: params.color || undefined,
          },
        })
      } catch (error) {
        console.error("Error updating tag", error)
        methods.setError("name", {
          type: "manual",
          message: `Error updating tag: ${String(error)}`,
        })
      }
    },
    [tag.id, workspaceId]
  )

  const onDelete = useCallback(async () => {
    try {
      await deleteTag({ tagId: tag.id, workspaceId })
      router.push(window.location.pathname)
    } catch (error) {
      console.error("Error deleting tag", error)
    }
  }, [tag.id, workspaceId])

  switch (action) {
    case TagItemAction.Delete:
      return (
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Are you sure?</AlertDialogTitle>
            <AlertDialogDescription>
              You are about to delete the tag{" "}
              <span className="font-bold text-foreground">{tag.name}</span>.
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="flex items-center gap-2"
              onClick={onDelete}
            >
              <Trash2Icon className="size-4" />
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      )
    case TagItemAction.Edit:
      return (
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Edit Tag</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogDescription>
            Edit the name and color of the tag.
          </AlertDialogDescription>
          <Form {...methods}>
            <form onSubmit={methods.handleSubmit(onEdit)}>
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
                  defaultValue={tag.color}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-sm">Color</FormLabel>
                      <div className="flex gap-2">
                        <FormControl>
                          <Input
                            className="max-w-24 font-mono text-sm"
                            placeholder="#000000"
                            {...field}
                            value={field.value ?? "#000000"}
                          />
                        </FormControl>
                        <ColorPicker
                          value={field.value ?? "#000000"}
                          onChange={(color) => field.onChange(color)}
                        />
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    type="submit"
                    className="flex items-center gap-2"
                  >
                    <PencilIcon className="size-4" />
                    Save Changes
                  </AlertDialogAction>
                </AlertDialogFooter>
              </div>
            </form>
          </Form>
        </AlertDialogContent>
      )
    default:
      return null
  }
}

function TagItem({
  tag,
  onSelect,
}: {
  tag: TagRead
  onSelect: (id: string) => void
}) {
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [action, setAction] = useState<TagItemAction | null>(null)

  return (
    <AlertDialog>
      <DropdownMenu open={isDropdownOpen} onOpenChange={setIsDropdownOpen}>
        <div className="group relative">
          <div
            onClick={() => onSelect(tag.id)}
            className={cn(
              "flex w-full items-center gap-2 rounded-md px-2 py-1 text-sm hover:cursor-pointer hover:bg-accent hover:text-accent-foreground",
              isDropdownOpen && "bg-accent text-accent-foreground"
            )}
          >
            <Tag
              className="size-4 stroke-white"
              style={{
                fill: tag.color || "rgb(var(--muted-foreground) / 0.8)",
              }}
            />
            <span className="truncate text-xs font-medium">{tag.name}</span>

            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className={cn(
                  "absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1",
                  "opacity-0 group-hover:opacity-100",
                  isDropdownOpen && "opacity-100",
                  "hover:bg-transparent focus:ring-0 focus-visible:ring-0"
                )}
                onClick={(e) => e.stopPropagation()}
              >
                <EllipsisIcon className="size-3 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side="right">
              <DropdownMenuLabel className="text-xs">Actions</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <AlertDialogTrigger asChild>
                <DropdownMenuItem
                  className="text-xs"
                  onClick={(e) => {
                    e.stopPropagation()
                    setAction(TagItemAction.Edit)
                  }}
                >
                  <PencilIcon className="mr-2 size-3" />
                  Edit
                </DropdownMenuItem>
              </AlertDialogTrigger>
              <AlertDialogTrigger asChild>
                <DropdownMenuItem
                  className="text-xs text-destructive"
                  onClick={(e) => {
                    e.stopPropagation()
                    setAction(TagItemAction.Delete)
                  }}
                >
                  <Trash2Icon className="mr-2 size-3" />
                  Delete
                </DropdownMenuItem>
              </AlertDialogTrigger>
            </DropdownMenuContent>
          </div>
        </div>
      </DropdownMenu>
      <TagItemActionDialogContent action={action} tag={tag} />
    </AlertDialog>
  )
}
