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
import { useParams } from "next/navigation"
import { useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { FieldType } from "@/client"
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
import { useEntities } from "@/lib/hooks/use-entities"

const fieldTypes: {
  value: FieldType
  label: string
  icon: React.ElementType
}[] = [
  { value: "TEXT", label: "Text", icon: Type },
  { value: "INTEGER", label: "Integer", icon: Hash },
  { value: "NUMBER", label: "Number", icon: DecimalsArrowRight },
  { value: "BOOL", label: "Boolean", icon: ToggleLeft },
  { value: "JSON", label: "JSON", icon: Brackets },
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
    "JSON",
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
  default_value: z.any().optional(),
  enum_options: z.array(z.string()).optional(),
  relation_settings: z
    .object({
      relation_type: z.enum(["belongs_to", "has_many"]),
      target_entity_id: z.string(),
      // v1: Relations are unidirectional, cascade delete is always true
    })
    .optional(),
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
  onSubmit: (data: CreateFieldFormData) => Promise<void>
  errorMessage?: string
}

export function CreateFieldDialog({
  open,
  onOpenChange,
  onSubmit,
  errorMessage,
}: CreateFieldDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false)
  const params = useParams<{ workspaceId: string }>()
  const { entities } = useEntities(params?.workspaceId || "")

  const form = useForm<CreateFieldFormData>({
    resolver: zodResolver(createFieldSchema),
    defaultValues: {
      field_key: "",
      field_type: "TEXT" as const,
      display_name: "",
      description: "",
      default_value: "",
      enum_options: [],
      relation_settings: {
        relation_type: "belongs_to",
        target_entity_id: "",
      },
    },
  })

  const fieldType = form.watch("field_type")
  const supportsPrimitive = useMemo(
    () => PRIMITIVE_FIELD_TYPES.includes(fieldType),
    [fieldType]
  )
  const isSelectField = useMemo(
    () => fieldType === "SELECT" || fieldType === "MULTI_SELECT",
    [fieldType]
  )
  const isRelationField = useMemo(
    () =>
      fieldType === "RELATION_BELONGS_TO" || fieldType === "RELATION_HAS_MANY",
    [fieldType]
  )

  const handleSubmit = async (data: CreateFieldFormData) => {
    setIsSubmitting(true)
    try {
      // Convert default value based on field type
      let processedData = { ...data }

      // Clean up empty values - set to undefined for proper API handling
      if (!processedData.description || processedData.description === "") {
        delete processedData.description
      }

      // Handle default value conversion and cleanup
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
        } else if (data.field_type === "JSON") {
          // Parse JSON string to object/array
          try {
            processedData.default_value = JSON.parse(
              data.default_value as string
            )
          } catch {
            // If invalid JSON, keep as-is and let backend validate
            processedData.default_value = data.default_value
          }
        }
        // Keep the default_value for TEXT and SELECT types as-is
      } else {
        // Remove default_value if not supported, empty, or "_none"
        delete processedData.default_value
      }

      // Process enum_options for SELECT/MULTI_SELECT fields
      if (data.field_type === "SELECT" || data.field_type === "MULTI_SELECT") {
        if (data.enum_options && data.enum_options.length > 0) {
          processedData.enum_options = data.enum_options
        } else {
          // SELECT/MULTI_SELECT require at least one option
          console.error(
            "SELECT/MULTI_SELECT fields require at least one option"
          )
          throw new Error("Please add at least one option for this field type")
        }
      } else {
        // Ensure enum_options is completely removed for non-select fields
        delete processedData.enum_options
      }

      // Process relation_settings for RELATION fields
      if (
        data.field_type === "RELATION_BELONGS_TO" ||
        data.field_type === "RELATION_HAS_MANY"
      ) {
        if (data.relation_settings?.target_entity_id) {
          processedData.relation_settings = {
            relation_type:
              data.field_type === "RELATION_BELONGS_TO"
                ? "belongs_to"
                : "has_many",
            target_entity_id: data.relation_settings.target_entity_id,
            // v1: No backref or cascade config
          }
        } else {
          // Relation fields require a target entity
          console.error(
            "Relation fields require a target entity to be selected"
          )
          throw new Error(
            "Please select a target entity for this relation field"
          )
        }
      } else {
        delete processedData.relation_settings
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
            {errorMessage && (
              <p className="text-sm font-medium text-destructive">
                {errorMessage}
              </p>
            )}
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
              {isSelectField && (
                <FormField
                  control={form.control}
                  name="enum_options"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Options</FormLabel>
                      <FormControl>
                        <MultiTagCommandInput
                          value={field.value || []}
                          onChange={field.onChange}
                          placeholder="Add options..."
                          allowCustomTags={true}
                          disableSuggestions={true}
                          className="w-full"
                          searchKeys={["label"]}
                        />
                      </FormControl>
                      <FormDescription>
                        Add the available options for this field
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              {isRelationField && (
                <>
                  <FormField
                    control={form.control}
                    name="relation_settings.target_entity_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Target entity</FormLabel>
                        <Select
                          onValueChange={field.onChange}
                          value={field.value}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {entities?.map((entity) => (
                              <SelectItem key={entity.id} value={entity.id}>
                                {entity.display_name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormDescription>
                          Link this entity to another entity via a relation
                          field
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              )}
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
    </TooltipProvider>
  )
}
