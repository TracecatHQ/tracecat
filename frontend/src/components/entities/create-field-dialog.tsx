"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { FieldType } from "@/client"
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
import { Textarea } from "@/components/ui/textarea"

const fieldTypes: { value: FieldType; label: string }[] = [
  { value: "TEXT", label: "Text" },
  { value: "INTEGER", label: "Integer" },
  { value: "NUMBER", label: "Number" },
  { value: "BOOL", label: "Boolean" },
  { value: "DATE", label: "Date" },
  { value: "DATETIME", label: "DateTime" },
  { value: "SELECT", label: "Select" },
  { value: "MULTI_SELECT", label: "Multi-select" },
  { value: "ARRAY_TEXT", label: "Text Array" },
  { value: "ARRAY_INTEGER", label: "Integer Array" },
  { value: "ARRAY_NUMBER", label: "Number Array" },
]

const createFieldSchema = z.object({
  field_key: z
    .string()
    .min(1, "Field key is required")
    .regex(
      /^[a-z][a-z0-9_]*$/,
      "Field key must start with a letter, be lowercase, and contain only letters, numbers, and underscores"
    ),
  field_type: z.enum([
    "TEXT",
    "INTEGER",
    "NUMBER",
    "BOOL",
    "DATE",
    "DATETIME",
    "SELECT",
    "MULTI_SELECT",
    "ARRAY_TEXT",
    "ARRAY_INTEGER",
    "ARRAY_NUMBER",
  ] as const),
  display_name: z.string().min(1, "Display name is required"),
  description: z.string().optional(),
  is_required: z.boolean().default(false),
  is_unique: z.boolean().default(false),
})

type CreateFieldFormData = z.infer<typeof createFieldSchema>

interface CreateFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: CreateFieldFormData) => Promise<void>
}

export function CreateFieldDialog({
  open,
  onOpenChange,
  onSubmit,
}: CreateFieldDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<CreateFieldFormData>({
    resolver: zodResolver(createFieldSchema),
    defaultValues: {
      field_key: "",
      field_type: "TEXT" as const,
      display_name: "",
      description: "",
      is_required: false,
      is_unique: false,
    },
  })

  const handleSubmit = async (data: CreateFieldFormData) => {
    setIsSubmitting(true)
    try {
      await onSubmit(data)
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to create field:", error)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Add field</DialogTitle>
          <DialogDescription>Add a new field to this entity.</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="field_key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Field key</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="customer_name"
                      {...field}
                      onChange={(e) =>
                        field.onChange(e.target.value.toLowerCase())
                      }
                    />
                  </FormControl>
                  <FormDescription>
                    Unique identifier for the field (lowercase, no spaces)
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="display_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Display name</FormLabel>
                  <FormControl>
                    <Input placeholder="Customer Name" {...field} />
                  </FormControl>
                  <FormDescription>
                    Human-readable name for the field
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="field_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Field type</FormLabel>
                  <Select
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a field type" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {fieldTypes.map((type) => (
                        <SelectItem key={type.value} value={type.value}>
                          {type.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    The data type for this field
                  </FormDescription>
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
                      placeholder="A brief description of the field"
                      className="resize-none"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="space-y-3">
              <FormField
                control={form.control}
                name="is_required"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-start space-x-3 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <div className="space-y-1 leading-none">
                      <FormLabel>Required field</FormLabel>
                      <FormDescription>
                        This field must have a value
                      </FormDescription>
                    </div>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="is_unique"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-start space-x-3 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <div className="space-y-1 leading-none">
                      <FormLabel>Unique values</FormLabel>
                      <FormDescription>
                        Each record must have a unique value for this field
                      </FormDescription>
                    </div>
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Creating..." : "Create field"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
