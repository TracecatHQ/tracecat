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
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { EntityFieldCreate, FieldType } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
  errorMessage?: string
}

export function CreateFieldDialog({
  open,
  onOpenChange,
  onSubmit,
  errorMessage,
}: CreateFieldDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

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
    setIsSubmitting(true)
    try {
      setSubmitError(null)
      const processed = {
        key: data.key,
        type: data.type,
        display_name: data.display_name,
      } as EntityFieldCreate

      if (data.description && data.description !== "") {
        processed.description = data.description
      }

      if (
        data.default_value !== undefined &&
        data.default_value !== "" &&
        data.default_value !== "_none"
      ) {
        if (data.type === "INTEGER") {
          processed.default_value = parseInt(data.default_value as string, 10)
        } else if (data.type === "NUMBER") {
          processed.default_value = parseFloat(data.default_value as string)
        } else if (data.type === "BOOL") {
          processed.default_value = data.default_value === "true"
        } else if (data.type === "MULTI_SELECT") {
          const value = data.default_value as string
          processed.default_value = value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        } else if (data.type === "JSON") {
          try {
            processed.default_value = JSON.parse(data.default_value as string)
          } catch {
            processed.default_value = data.default_value
          }
        }
      } else {
        delete processed.default_value
      }

      if (isSelectField) {
        if (data.options && data.options.length > 0) {
          processed.options = data.options.map((label) => ({ label }))
        } else {
          throw new Error("Please add at least one option for this field type")
        }
      } else {
        delete processed.options
      }

      await onSubmit(processed)
      form.reset()
      onOpenChange(false)
    } catch (error: unknown) {
      console.error("Failed to create field:", error)
      let message = "Failed to create the field. Please try again."
      const err = error as {
        body?: { detail?: string | string[]; message?: string; error?: string }
        message?: string
        status?: number
        statusText?: string
      }
      const detail = err?.body?.detail
      if (Array.isArray(detail)) message = detail.join("\n")
      else
        message =
          (typeof detail === "string" && detail) ||
          err?.body?.message ||
          err?.body?.error ||
          (err?.status && err?.statusText
            ? `${err.status} ${err.statusText}`
            : err?.message) ||
          message
      setSubmitError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  useEffect(() => {
    if (!open) setSubmitError(null)
  }, [open])

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
            {(submitError || errorMessage) && (
              <Alert variant="destructive">
                <AlertTitle>Failed to create field</AlertTitle>
                <AlertDescription>
                  {submitError || errorMessage}
                </AlertDescription>
              </Alert>
            )}
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
