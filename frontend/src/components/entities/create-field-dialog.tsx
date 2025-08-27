"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  Braces,
  Calendar,
  CalendarClock,
  CircleDot,
  Hash,
  Info,
  ListTodo,
  SquareCheck,
  ToggleLeft,
  Type,
} from "lucide-react"
import { useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { EntityFieldCreate, FieldType } from "@/client"
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

/**
 * Parses a default value string based on the field type.
 *
 * This function handles type conversion for different field types:
 * - INTEGER: Parses string to integer using parseInt
 * - NUMBER: Parses string to floating point number using parseFloat
 * - BOOL: Converts string "true" to boolean true, anything else to false
 * - MULTI_SELECT: Splits comma-separated string into trimmed array of strings
 * - JSON: Attempts to parse as JSON, falls back to original value if parsing fails
 * - Other types: Returns the value as-is
 *
 * @param fieldType - The type of field to parse the value for
 * @param value - The raw value to parse (typically a string from form input)
 * @returns The parsed value in the appropriate type, or undefined if value is empty/invalid
 */
function parseDefaultValue(
  fieldType: FieldType,
  value: string | number | boolean | undefined
): string | number | boolean | string[] | unknown | undefined {
  // Check if value is empty or should be ignored
  if (value === undefined || value === "" || value === "_none") {
    return undefined
  }

  switch (fieldType) {
    case "INTEGER":
      return parseInt(value as string, 10)

    case "NUMBER":
      return parseFloat(value as string)

    case "BOOL":
      return value === "true"

    case "MULTI_SELECT":
      return (value as string)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)

    case "JSON":
      try {
        return JSON.parse(value as string)
      } catch {
        // If JSON parsing fails, return the original value
        return value
      }

    default:
      // For TEXT, DATE, DATETIME, SELECT, and other types
      return value
  }
}

const fieldTypes: {
  value: FieldType
  label: string
  icon: React.ElementType
}[] = [
  { value: "TEXT", label: "Text", icon: Type },
  { value: "INTEGER", label: "Integer", icon: Hash },
  { value: "NUMBER", label: "Number", icon: CircleDot },
  { value: "BOOL", label: "Boolean", icon: ToggleLeft },
  { value: "JSON", label: "JSON", icon: Braces },
  { value: "DATE", label: "Date", icon: Calendar },
  { value: "DATETIME", label: "Date and time", icon: CalendarClock },
  { value: "SELECT", label: "Select", icon: SquareCheck },
  { value: "MULTI_SELECT", label: "Multi-select", icon: ListTodo },
]

const createFieldSchema = z.object({
  key: z
    .string()
    .min(1, "Field key is required")
    .regex(
      /^[a-z][a-z0-9_]*$/,
      "Field key must start with a letter, be lowercase, and contain only letters, numbers, and underscores"
    ),
  type: z.enum([
    "TEXT",
    "INTEGER",
    "NUMBER",
    "BOOL",
    "JSON",
    "DATE",
    "DATETIME",
    "SELECT",
    "MULTI_SELECT",
  ] as const),
  display_name: z.string().min(1, "Display name is required"),
  description: z.string().optional(),
  default_value: z.any().optional(),
  options: z.array(z.string()).optional(),
})

type CreateFieldFormData = z.infer<typeof createFieldSchema>

// Primitive field types that support default values
const PRIMITIVE_FIELD_TYPES: FieldType[] = [
  "TEXT",
  "INTEGER",
  "NUMBER",
  "BOOL",
  "JSON",
  "SELECT",
  "MULTI_SELECT",
]

interface CreateFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: EntityFieldCreate) => Promise<void>
  isSubmitting?: boolean
}

export function CreateFieldDialog({
  open,
  onOpenChange,
  onSubmit,
  isSubmitting = false,
}: CreateFieldDialogProps) {
  const form = useForm<CreateFieldFormData>({
    resolver: zodResolver(createFieldSchema),
    defaultValues: {
      key: "",
      type: "TEXT" as const,
      display_name: "",
      description: "",
      default_value: "",
      options: [],
    },
  })

  const fieldType = form.watch("type")
  const supportsPrimitive = useMemo(
    () => PRIMITIVE_FIELD_TYPES.includes(fieldType),
    [fieldType]
  )
  const isSelectField = useMemo(
    () => fieldType === "SELECT" || fieldType === "MULTI_SELECT",
    [fieldType]
  )

  const handleSubmit = async (data: CreateFieldFormData) => {
    const processed = {
      key: data.key,
      type: data.type,
      display_name: data.display_name,
    } as EntityFieldCreate

    if (data.description && data.description !== "") {
      processed.description = data.description
    }

    // Parse the default value based on the field type
    const parsedDefaultValue = parseDefaultValue(data.type, data.default_value)
    if (parsedDefaultValue !== undefined) {
      processed.default_value = parsedDefaultValue
    }

    if (isSelectField) {
      if (!data.options || data.options.length === 0) {
        form.setError("options", {
          type: "manual",
          message: "Please add at least one option for this field type",
        })
        return
      }
      processed.options = data.options.map((label) => ({ label }))
    } else {
      delete processed.options
    }

    await onSubmit(processed)
    form.reset()
    onOpenChange(false)
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
              name="key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Key</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Alphanumeric lowercase with underscores"
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
              name="type"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center gap-2">
                    <FormLabel>Data type</FormLabel>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Info className="h-4 w-4 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>* Supports default value on entity creation</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
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
                        const supportsDefault = PRIMITIVE_FIELD_TYPES.includes(
                          type.value
                        )
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
                      className="resize-none text-xs"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {isSelectField && (
              <FormField
                control={form.control}
                name="options"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Options</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={field.value || []}
                        onChange={field.onChange}
                        placeholder="Add options..."
                        allowCustomTags
                        disableSuggestions
                        className="w-full"
                        searchKeys={["label"]}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}
            {supportsPrimitive && (
              <FormField
                control={form.control}
                name="default_value"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Default value</FormLabel>
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
                                  : fieldType === "JSON"
                                    ? '{"key": "value"} or []'
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
