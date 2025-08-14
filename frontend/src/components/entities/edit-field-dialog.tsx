"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { FieldMetadataRead, FieldMetadataUpdate } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
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

const editFieldSchema = z.object({
  display_name: z.string().min(1, "Display name is required"),
  description: z.string().optional(),
  enum_options: z.array(z.string()).optional(),
})

type EditFieldSchema = z.infer<typeof editFieldSchema>

interface EditFieldDialogProps {
  field: FieldMetadataRead | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (fieldId: string, data: FieldMetadataUpdate) => Promise<void>
  isPending?: boolean
}

export function EditFieldDialog({
  field,
  open,
  onOpenChange,
  onSubmit,
  isPending,
}: EditFieldDialogProps) {
  const form = useForm<EditFieldSchema>({
    resolver: zodResolver(editFieldSchema),
    values: {
      display_name: field?.display_name || "",
      description: field?.description || "",
      enum_options: field?.enum_options || [],
    },
  })

  const isSelectField = useMemo(
    () =>
      field?.field_type === "SELECT" || field?.field_type === "MULTI_SELECT",
    [field?.field_type]
  )

  const handleSubmit = async (data: EditFieldSchema) => {
    if (!field) return

    try {
      const updateData: FieldMetadataUpdate = {
        display_name: data.display_name,
        description: data.description || null,
      }

      // Only include enum_options for SELECT/MULTI_SELECT fields
      if (isSelectField) {
        if (data.enum_options && data.enum_options.length > 0) {
          updateData.enum_options = data.enum_options
        } else {
          // SELECT/MULTI_SELECT require at least one option
          console.error(
            "SELECT/MULTI_SELECT fields require at least one option"
          )
          throw new Error("Please add at least one option for this field type")
        }
      }

      await onSubmit(field.id, updateData)
      onOpenChange(false)
      form.reset()
    } catch (error) {
      console.error("Failed to update field:", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit field</DialogTitle>
          <DialogDescription>
            Update the name and description for this field.
          </DialogDescription>
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
                  value={field?.field_key || ""}
                  disabled
                  className="bg-muted text-xs"
                />
              </FormControl>
              <FormDescription className="text-xs">
                The field key cannot be changed after creation
              </FormDescription>
            </FormItem>

            <FormItem>
              <FormLabel>Data type</FormLabel>
              <FormControl>
                <Input
                  value={field?.field_type || ""}
                  disabled
                  className="bg-muted text-xs"
                />
              </FormControl>
              <FormDescription className="text-xs">
                The field type cannot be changed after creation
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
                      {...field}
                      className="text-xs"
                      placeholder="Write a short human-readable name"
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
                      {...field}
                      placeholder="Write a description"
                      className="resize-none text-xs"
                      rows={3}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {isSelectField && (
              <FormField
                control={form.control}
                name="enum_options"
                render={({ field: formField }) => (
                  <FormItem>
                    <FormLabel>Options</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={formField.value || []}
                        onChange={formField.onChange}
                        placeholder="Add options..."
                        allowCustomTags={true}
                        disableSuggestions={true}
                        className="w-full"
                        searchKeys={["label"]}
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      Add or remove available options for this field
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

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
