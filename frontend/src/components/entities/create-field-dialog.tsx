"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  BookA,
  Brackets,
  Calendar,
  CalendarClock,
  DecimalsArrowRight,
  GitBranch,
  Hash,
  Info,
  Link2,
  ListOrdered,
  ListTodo,
  SquareCheck,
  ToggleLeft,
  Type,
} from "lucide-react"
import { useMemo, useState } from "react"
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

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
  { value: "RELATION_BELONGS_TO", label: "Belongs to", icon: Link2 },
  { value: "RELATION_HAS_MANY", label: "Has many", icon: GitBranch },
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
    "RELATION_BELONGS_TO",
    "RELATION_HAS_MANY",
  ] as const),
  display_name: z.string().min(1, "Display name is required"),
  description: z.string().optional(),
  is_required: z.boolean().default(false),
  is_unique: z.boolean().default(false),
  default_value: z.any().optional(),
})

type CreateFieldFormData = z.infer<typeof createFieldSchema>

// Primitive field types that support default values
const PRIMITIVE_FIELD_TYPES: FieldType[] = [
  "TEXT",
  "INTEGER",
  "NUMBER",
  "BOOL",
  "SELECT",
  "MULTI_SELECT",
]

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
      default_value: undefined,
    },
  })

  const fieldType = form.watch("field_type")
  const supportsPrimitive = useMemo(
    () => PRIMITIVE_FIELD_TYPES.includes(fieldType),
    [fieldType]
  )

  const handleSubmit = async (data: CreateFieldFormData) => {
    setIsSubmitting(true)
    try {
      // Convert default value based on field type
      let processedData = { ...data }
      if (
        data.default_value !== undefined &&
        data.default_value !== "" &&
        data.default_value !== "_none"
      ) {
        if (data.field_type === "INTEGER") {
          processedData.default_value = parseInt(
            data.default_value as string,
            10
          )
        } else if (data.field_type === "NUMBER") {
          processedData.default_value = parseFloat(data.default_value as string)
        } else if (data.field_type === "BOOL") {
          processedData.default_value = data.default_value === "true"
        } else if (data.field_type === "MULTI_SELECT") {
          // Convert comma-separated string to array
          const value = data.default_value as string
          processedData.default_value = value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        }
      } else {
        // Remove default_value if not supported or empty
        delete processedData.default_value
      }

      await onSubmit(processedData)
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to create field:", error)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <TooltipProvider>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>Add field</DialogTitle>
            <DialogDescription>
              Add a new field to this entity.
            </DialogDescription>
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
                name="field_type"
                render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center gap-2">
                      <FormLabel>Data type</FormLabel>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Info className="h-4 w-4 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>* Supports default value on entity creation</p>
                        </TooltipContent>
                      </Tooltip>
                    </div>
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select a field type" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {fieldTypes.map((type) => {
                          const Icon = type.icon
                          const supportsDefault =
                            PRIMITIVE_FIELD_TYPES.includes(type.value)
                          return (
                            <SelectItem key={type.value} value={type.value}>
                              <div className="flex items-center gap-2">
                                <Icon className="h-4 w-4" />
                                <span>
                                  {type.label}
                                  {supportsDefault && " *"}
                                </span>
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
                        placeholder="Write a description"
                        className="text-xs resize-none"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {supportsPrimitive && (
                <FormField
                  control={form.control}
                  name="default_value"
                  render={({ field }) => (
                    <FormItem>
                      <div className="flex items-center gap-2">
                        <FormLabel>Default value</FormLabel>
                      </div>
                      <FormControl>
                        {fieldType === "BOOL" ? (
                          <Select
                            onValueChange={field.onChange}
                            value={field.value?.toString() || "_none"}
                          >
                            <SelectTrigger>
                              <SelectValue placeholder="Select default value" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="_none">No default</SelectItem>
                              <SelectItem value="true">True</SelectItem>
                              <SelectItem value="false">False</SelectItem>
                            </SelectContent>
                          </Select>
                        ) : (
                          <Input
                            placeholder={
                              fieldType === "INTEGER"
                                ? "Enter integer default"
                                : fieldType === "NUMBER"
                                  ? "Enter number default"
                                  : fieldType === "MULTI_SELECT"
                                    ? "Comma-separated values"
                                    : "Enter default value"
                            }
                            {...field}
                          />
                        )}
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
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
                      <div className="flex items-center gap-2">
                        <FormLabel>Required field</FormLabel>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Info className="h-4 w-4 text-muted-foreground" />
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>This field must have a value</p>
                          </TooltipContent>
                        </Tooltip>
                      </div>
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
                      <div className="flex items-center gap-2">
                        <FormLabel>Unique values</FormLabel>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Info className="h-4 w-4 text-muted-foreground" />
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>
                              Each record must have a unique value for this
                              field
                            </p>
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    </div>
                  </FormItem>
                )}
              />
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
    </TooltipProvider>
  )
}
