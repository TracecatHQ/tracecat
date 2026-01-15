"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { type ControllerRenderProps, useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  type SqlType as ApiSqlType,
  type CaseFieldReadMinimal,
  casesUpdateField,
} from "@/client"
import { SqlTypeDisplay } from "@/components/data-type/sql-type-display"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Button } from "@/components/ui/button"
import { DateTimePicker } from "@/components/ui/date-time-picker"
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
import { toast } from "@/components/ui/use-toast"
import type { SqlType } from "@/lib/data-type"
import type { TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

const isSelectableColumnType = (type?: string) =>
  type === "SELECT" || type === "MULTI_SELECT"

const NO_DEFAULT_SENTINEL = "__tracecat_no_default__"

const sanitizeColumnOptions = (options?: string[]) => {
  if (!options) return []
  const seen = new Set<string>()
  const cleaned: string[] = []
  for (const option of options) {
    const trimmed = option.trim()
    if (trimmed.length === 0 || seen.has(trimmed)) {
      continue
    }
    seen.add(trimmed)
    cleaned.push(trimmed)
  }
  return cleaned
}

const editCaseFieldFormSchema = z
  .object({
    name: z
      .string()
      .min(1, "Field name is required")
      .max(100, "Field name must be less than 100 characters")
      .refine(
        (value) => /^[a-zA-Z][a-zA-Z0-9_]*$/.test(value),
        "Field name must start with a letter and contain only letters, numbers, and underscores"
      ),
    default: z.string().nullable().optional(),
    defaultMulti: z.array(z.string()).optional(),
    options: z.array(z.string().min(1, "Option cannot be empty")).optional(),
  })
  .superRefine((data, ctx) => {
    // Note: We don't validate options requirements here since we can't access field.type
    // The form will handle this based on the field type passed to the component
    const sanitizedOptions = sanitizeColumnOptions(data.options)

    // If we have options and defaults, validate defaults are in options
    if (sanitizedOptions.length > 0 && data.default) {
      const defaultVal = data.default.trim()
      if (defaultVal.length > 0 && !sanitizedOptions.includes(defaultVal)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Default value must be one of the defined options",
          path: ["default"],
        })
      }
    }

    if (sanitizedOptions.length > 0 && data.defaultMulti) {
      for (const val of data.defaultMulti) {
        if (!sanitizedOptions.includes(val)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: `"${val}" is not one of the defined options`,
            path: ["defaultMulti"],
          })
          break
        }
      }
    }
  })

type EditCaseFieldFormValues = z.infer<typeof editCaseFieldFormSchema>

interface EditCustomFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  field: CaseFieldReadMinimal | null
}

export function EditCustomFieldDialog({
  open,
  onOpenChange,
  field,
}: EditCustomFieldDialogProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<EditCaseFieldFormValues>({
    resolver: zodResolver(editCaseFieldFormSchema),
    defaultValues: {
      name: "",
      default: null,
      defaultMulti: [],
      options: [],
    },
  })

  const requiresOptions = field ? isSelectableColumnType(field.type) : false

  // Reset form when field changes
  useEffect(() => {
    if (field && open) {
      const isMultiSelect = field.type === "MULTI_SELECT"
      let defaultMulti: string[] = []
      let defaultSingle: string | null = null

      if (field.default) {
        if (isMultiSelect) {
          try {
            const parsed = JSON.parse(field.default)
            if (Array.isArray(parsed)) {
              defaultMulti = parsed.filter(
                (item): item is string => typeof item === "string"
              )
            }
          } catch {
            // ignore parse errors
          }
        } else {
          defaultSingle = field.default
        }
      }

      form.reset({
        name: field.id,
        default: defaultSingle,
        defaultMulti,
        options: field.options || [],
      })
    }
  }, [field, form, open])

  const onSubmit = async (data: EditCaseFieldFormValues) => {
    if (!field) return
    setIsSubmitting(true)

    try {
      let defaultValue: string | number | boolean | string[] | null = null
      const rawDefault = data.default

      // Handle MULTI_SELECT separately - it uses defaultMulti (array)
      if (field.type === "MULTI_SELECT") {
        const multiDefaults = data.defaultMulti || []
        if (multiDefaults.length > 0) {
          defaultValue = multiDefaults
        }
      } else if (
        rawDefault !== null &&
        rawDefault !== undefined &&
        rawDefault !== ""
      ) {
        switch (field.type) {
          case "INTEGER": {
            const normalized =
              typeof rawDefault === "string" ? rawDefault.trim() : rawDefault
            if (typeof normalized === "string" && normalized.length === 0) {
              form.setError("default", {
                type: "manual",
                message: "Default must be a whole number",
              })
              setIsSubmitting(false)
              return
            }
            const parsed =
              typeof normalized === "number" ? normalized : Number(normalized)
            if (!Number.isInteger(parsed)) {
              form.setError("default", {
                type: "manual",
                message: "Default must be a whole number",
              })
              setIsSubmitting(false)
              return
            }
            defaultValue = parsed
            break
          }
          case "NUMERIC": {
            const parsed =
              typeof rawDefault === "number" ? rawDefault : Number(rawDefault)
            if (Number.isNaN(parsed)) {
              form.setError("default", {
                type: "manual",
                message: "Default must be a number",
              })
              setIsSubmitting(false)
              return
            }
            defaultValue = parsed
            break
          }
          case "BOOLEAN": {
            const normalized = String(rawDefault).trim().toLowerCase()
            if (normalized === "true" || normalized === "1") {
              defaultValue = true
            } else if (normalized === "false" || normalized === "0") {
              defaultValue = false
            } else {
              form.setError("default", {
                type: "manual",
                message: "Use true, false, 1, or 0",
              })
              setIsSubmitting(false)
              return
            }
            break
          }
          case "TIMESTAMPTZ":
          case "TIMESTAMP": {
            const iso =
              typeof rawDefault === "string"
                ? rawDefault
                : new Date(rawDefault).toISOString()
            const parsed = new Date(iso)
            if (Number.isNaN(parsed.getTime())) {
              form.setError("default", {
                type: "manual",
                message: "Select a valid date and time",
              })
              setIsSubmitting(false)
              return
            }
            defaultValue = parsed.toISOString()
            break
          }
          case "SELECT": {
            defaultValue = String(rawDefault)
            break
          }
          default: {
            defaultValue = String(rawDefault).trim()
          }
        }
      }

      await casesUpdateField({
        workspaceId,
        fieldId: field.id,
        requestBody: {
          name: data.name !== field.id ? data.name : undefined,
          default: defaultValue,
          options: isSelectableColumnType(field.type)
            ? sanitizeColumnOptions(data.options)
            : undefined,
        },
      })

      queryClient.invalidateQueries({
        queryKey: ["case-fields", workspaceId],
      })

      toast({
        title: "Field updated",
        description: "The case field was updated successfully.",
      })

      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to update case field", error)
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError
        if (apiError.status === 409) {
          form.setError("name", {
            type: "manual",
            message: "A field with this name already exists",
          })
        } else {
          toast({
            title: "Error updating field",
            description:
              (apiError.body as { detail?: string })?.detail ||
              "Failed to update the case field. Please try again.",
          })
        }
      } else {
        toast({
          title: "Error updating field",
          description: "Failed to update the case field. Please try again.",
        })
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  if (!field) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Edit custom field</DialogTitle>
          <DialogDescription>
            Modify the properties of this custom field.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field: formField }) => (
                <FormItem>
                  <FormLabel>Identifier / Slug</FormLabel>
                  <FormControl>
                    <Input {...formField} />
                  </FormControl>
                  <FormDescription>
                    A human readable ID of the field. Use snake_case for best
                    compatibility.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormItem>
              <FormLabel>Data type</FormLabel>
              <div className="flex items-center gap-2 rounded-md border border-input bg-muted/50 px-3 py-2">
                <SqlTypeDisplay
                  type={field.type as SqlType}
                  labelClassName="text-xs"
                />
                <span className="text-xs text-muted-foreground">
                  (cannot be changed)
                </span>
              </div>
            </FormItem>

            {requiresOptions && (
              <FormField
                control={form.control}
                name="options"
                render={({ field: formField }) => (
                  <FormItem>
                    <FormLabel>Options</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={formField.value || []}
                        onChange={formField.onChange}
                        placeholder="Add allowed values..."
                        allowCustomTags
                        disableSuggestions
                        className="w-full"
                        searchKeys={["label"]}
                      />
                    </FormControl>
                    <FormDescription>
                      Define the allowed values for this field.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            {field.type === "MULTI_SELECT" ? (
              <FormField
                control={form.control}
                name="defaultMulti"
                render={({ field: formField }) => (
                  <FormItem>
                    <FormLabel>Default values (optional)</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={formField.value || []}
                        onChange={formField.onChange}
                        placeholder="Select default values..."
                        suggestions={sanitizeColumnOptions(
                          form.watch("options")
                        ).map((opt) => ({ id: opt, label: opt, value: opt }))}
                        searchKeys={["label"]}
                        className="w-full"
                      />
                    </FormControl>
                    <FormDescription>
                      Select default values from the options above.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : (
              <FormField
                control={form.control}
                name="default"
                render={({ field: formField }) => (
                  <FormItem>
                    <FormLabel>Default value (optional)</FormLabel>
                    <FormControl>
                      <DefaultValueInput
                        type={field.type}
                        field={formField}
                        options={form.watch("options")}
                      />
                    </FormControl>
                    <FormDescription>
                      {getDefaultHelperText(field.type)}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Saving..." : "Save changes"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function getDefaultHelperText(type: ApiSqlType | undefined) {
  switch (type) {
    case "INTEGER":
      return "Optional whole number that fills in missing values."
    case "NUMERIC":
      return "Optional numeric value (decimals allowed) used when none is provided."
    case "BOOLEAN":
      return "Accepts true, false, 1, or 0. Leave blank to omit a default."
    case "TIMESTAMPTZ":
    case "TIMESTAMP":
      return "Select an ISO8601 date and time (stored in UTC)."
    case "SELECT":
      return "Select a default value from the options above."
    default:
      return "Optional text used when no value is supplied."
  }
}

function DefaultValueInput({
  type,
  field,
  options,
}: {
  type: ApiSqlType | undefined
  field: ControllerRenderProps<EditCaseFieldFormValues, "default">
  options?: string[]
}) {
  const resolvedType: ApiSqlType = type ?? "TEXT"
  const sanitizedOptions = sanitizeColumnOptions(options)

  switch (resolvedType) {
    case "INTEGER":
      return (
        <Input
          type="number"
          step={1}
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="Enter an integer"
        />
      )
    case "NUMERIC":
      return (
        <Input
          type="number"
          step="any"
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="Enter a number"
        />
      )
    case "BOOLEAN":
      return (
        <Input
          type="text"
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="true, false, 1, or 0"
        />
      )
    case "TIMESTAMPTZ":
    case "TIMESTAMP": {
      const stringValue =
        typeof field.value === "string" && field.value.length > 0
          ? field.value
          : undefined
      const parsedDate =
        stringValue !== undefined ? new Date(stringValue) : null
      const dateValue =
        parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate : null

      return (
        <DateTimePicker
          value={dateValue}
          onChange={(next) => field.onChange(next ? next.toISOString() : "")}
          onBlur={field.onBlur}
          buttonProps={{ className: "w-full" }}
        />
      )
    }
    case "SELECT":
      return (
        <Select
          value={field.value || NO_DEFAULT_SENTINEL}
          onValueChange={(value) =>
            field.onChange(value === NO_DEFAULT_SENTINEL ? "" : value)
          }
        >
          <SelectTrigger>
            <SelectValue placeholder="Select a default value" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_DEFAULT_SENTINEL}>No default</SelectItem>
            {sanitizedOptions.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    default:
      return (
        <Input
          type="text"
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="Enter default text"
        />
      )
  }
}
