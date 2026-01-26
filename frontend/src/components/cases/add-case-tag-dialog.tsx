"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import z from "zod"
import type { TagCreate } from "@/client"
import { ColorPicker } from "@/components/color-picker"
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
import { useCaseTagCatalog } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

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

interface AddCaseTagDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AddCaseTagDialog({
  open,
  onOpenChange,
}: AddCaseTagDialogProps) {
  const workspaceId = useWorkspaceId()
  const { caseTags, createCaseTag } = useCaseTagCatalog(workspaceId)

  const methods = useForm<TagCreate>({
    resolver: zodResolver(createTagSchema),
    defaultValues: {
      name: "",
      color: "#aabbcc",
    },
  })

  const handleCreateTag = async (params: TagCreate) => {
    try {
      const tagExists = caseTags?.some(
        (tag) => tag.name.toLowerCase() === params.name.trim().toLowerCase()
      )

      if (tagExists) {
        methods.setError("name", {
          type: "manual",
          message: "A tag with this name already exists",
        })
        return
      }

      await createCaseTag({
        workspaceId,
        requestBody: params,
      })
      methods.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Error creating case tag", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create new case tag</DialogTitle>
          <DialogDescription>
            Enter a name for your new case tag.
          </DialogDescription>
        </DialogHeader>
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(handleCreateTag)}>
            <div className="space-y-4">
              <FormField
                key="name"
                control={methods.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm">Name</FormLabel>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Name"
                        {...field}
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
                  Create tag
                </Button>
              </DialogFooter>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
