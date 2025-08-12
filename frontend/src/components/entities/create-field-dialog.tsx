"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  BookA,
  Brackets,
  Calendar,
  CalendarClock,
  DecimalsArrowRight,
  Hash,
  ListOrdered,
  ListTodo,
  SquareCheck,
  ToggleLeft,
  Type,
} from "lucide-react"
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

const fieldTypes: {
  value: FieldType
  label: string
  icon: React.ElementType
}[] = [
  { value: "TEXT", label: "Text", icon: Type },
  { value: "INTEGER", label: "Integer", icon: Hash },
  { value: "NUMBER", label: "Number", icon: DecimalsArrowRight },
  { value: "BOOL", label: "Boolean", icon: ToggleLeft },
  { value: "DATE", label: "Date", icon: Calendar },
  { value: "DATETIME", label: "Date and time", icon: CalendarClock },
  { value: "SELECT", label: "Select", icon: SquareCheck },
  { value: "MULTI_SELECT", label: "Multi-select", icon: ListTodo },
  { value: "ARRAY_TEXT", label: "Text array", icon: BookA },
  { value: "ARRAY_INTEGER", label: "Integer array", icon: ListOrdered },
  { value: "ARRAY_NUMBER", label: "Number array", icon: Brackets },
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
                  <FormLabel>Identifier / Slug</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Lowercase, no spaces"
                      {...field}
                      onChange={(e) =>
                        field.onChange(e.target.value.toLowerCase())
                      }
                    />
                  </FormControl>
                  <FormDescription>
                    This cannot be changed after creation
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
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder="Short human-readable name" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="field_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Data type</FormLabel>
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
                      {fieldTypes.map((type) => {
                        const Icon = type.icon
                        return (
                          <SelectItem key={type.value} value={type.value}>
                            <div className="flex items-center gap-2">
                              <Icon className="h-4 w-4" />
                              <span>{type.label}</span>
                            </div>
                          </SelectItem>
                        )
                      })}
                    </SelectContent>
                  </Select>
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
