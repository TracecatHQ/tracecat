"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { EntityRead } from "@/client"
import { IconPicker } from "@/components/form/icon-picker"
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
import { Textarea } from "@/components/ui/textarea"

const schema = z.object({
  display_name: z.string().min(1, "Display name is required"),
  description: z.string().optional(),
  icon: z.string().optional(),
})

export type EditEntityForm = z.infer<typeof schema>

interface EditEntityDialogProps {
  entity: EntityRead | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: EditEntityForm) => Promise<void>
  isPending?: boolean
}

export function EditEntityDialog({
  entity,
  open,
  onOpenChange,
  onSubmit,
  isPending,
}: EditEntityDialogProps) {
  const form = useForm<EditEntityForm>({
    resolver: zodResolver(schema),
    values: {
      display_name: entity?.display_name || "",
      description: entity?.description || "",
      icon: entity?.icon || "",
    },
  })

  useEffect(() => {
    form.reset({
      display_name: entity?.display_name || "",
      description: entity?.description || "",
      icon: entity?.icon || "",
    })
  }, [entity])

  const handleSubmit = async (data: EditEntityForm) => {
    await onSubmit({
      display_name: data.display_name,
      description: data.description || "",
      icon: data.icon || "",
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Edit entity</DialogTitle>
          <DialogDescription>Update entity details.</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            <FormItem>
              <FormLabel>Identifier / Slug</FormLabel>
              <FormControl>
                <Input
                  value={entity?.key || ""}
                  disabled
                  className="bg-muted text-xs"
                />
              </FormControl>
              <FormDescription className="text-xs">
                The entity key cannot be changed after creation
              </FormDescription>
            </FormItem>

            <FormField
              control={form.control}
              name="display_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Write a short human-readable name"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea
                      className="text-xs resize-none"
                      placeholder="Write a description"
                      rows={3}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="icon"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Icon</FormLabel>
                  <FormControl>
                    <IconPicker
                      value={field.value}
                      onValueChange={field.onChange}
                      className="text-xs"
                      placeholder="Select an icon (optional)"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isPending}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending ? "Saving..." : "Save changes"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
