"use client"

import React, { useEffect } from "react"
import { FolderDirectoryItem } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { Pencil } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useFolders } from "@/lib/hooks"
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
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/loading/spinner"

const renameFolderSchema = z.object({
  name: z
    .string()
    .min(1, "Name is required")
    .max(100, "Name cannot exceed 100 characters")
    .regex(
      /^[a-zA-Z0-9_\-\s]+$/,
      "Name must contain only letters, numbers, spaces, underscores and hyphens"
    ),
})

type RenameFolderSchema = z.infer<typeof renameFolderSchema>

export function FolderRenameDialog({
  open,
  onOpenChange,
  selectedFolder,
  setSelectedFolder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedFolder: FolderDirectoryItem | null
  setSelectedFolder: (selectedFolder: FolderDirectoryItem | null) => void
}) {
  const { workspaceId } = useWorkspace()
  const { updateFolder, updateFolderIsPending } = useFolders(workspaceId)

  const form = useForm<RenameFolderSchema>({
    resolver: zodResolver(renameFolderSchema),
    defaultValues: {
      name: selectedFolder?.name || "",
    },
  })

  // Update form when selectedFolder changes
  useEffect(() => {
    if (selectedFolder) {
      form.reset({
        name: selectedFolder.name,
      })
    }
  }, [selectedFolder, form])

  const onSubmit = async (data: RenameFolderSchema) => {
    if (!selectedFolder) return

    try {
      await updateFolder({
        folderId: selectedFolder.id,
        name: data.name,
      })
      setSelectedFolder(null)
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to rename folder:", error)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedFolder(null)
        }
        onOpenChange(isOpen)
      }}
    >
      <DialogContent>
        <DialogHeader className="space-y-4">
          <DialogTitle>Rename Folder</DialogTitle>
          <DialogDescription>
            Enter a new name for the folder.
          </DialogDescription>
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
                    The new name for the folder.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="submit" disabled={updateFolderIsPending}>
                {updateFolderIsPending ? (
                  <Spinner />
                ) : (
                  <Pencil className="mr-2 size-4" />
                )}
                Rename Folder
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export const RenameFolderDialogTrigger = DialogTrigger
