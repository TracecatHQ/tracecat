"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"
import { type ControllerRenderProps, useForm } from "react-hook-form"
import { z } from "zod"
import { ApiError, type CaseFieldReadMinimal, casesUpdateField } from "@/client"
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
import type { TracecatApiError } from "@/lib/errors"
import { type SqlTypeCreatable, SqlTypeCreatableEnum } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

const isSelectableColumnType = (type?: string) =>
  type === "SELECT" || type === "MULTI_SELECT"

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

const isSqlTypeCreatable = (value: string): value is SqlTypeCreatable =>
  SqlTypeCreatableEnum.includes(value as SqlTypeCreatable)

const parseMultiSelectDefault = (value: string | null): string[] => {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed)) {
      return parsed.filter((item): item is string => typeof item === "string")
    }
  } catch {
    // Ignore parse errors and treat as no default values.
  }
  return []
}

const caseFieldFormSchema = z
  .object({
    name: z
      .string()
      .min(1, "Field name is required")
      .max(100, "Field name must be less than 100 characters")
      .refine(
        (value) => /^[a-zA-Z][a-zA-Z0-9_]*$/.test(value),
        "Field name must start with a letter and contain only letters, numbers, and underscores"
      ),
    type: z.enum(SqlTypeCreatableEnum),
    nullable: z.boolean().default(true),
    default: z.string().nullable().optional(),
    defaultMulti: z.array(z.string()).optional(),
    options: z.array(z.string().min(1, "Option cannot be empty")).optional(),
  })
  .superRefine((data, ctx) => {
    if (isSelectableColumnType(data.type)) {
      const sanitizedOptions = sanitizeColumnOptions(data.options)
      if (sanitizedOptions.length === 0) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Please add at least one option",
          path: ["options"],
        })
      }

      if (data.type === "SELECT") {
        const defaultVal = data.default?.trim()
        if (defaultVal && defaultVal.length > 0) {
          if (!sanitizedOptions.includes(defaultVal)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              message: "Default value must be one of the defined options",
              path: ["default"],
            })
          }
        }
      } else if (data.type === "MULTI_SELECT") {
        const defaultVals = data.defaultMulti || []
        for (const val of defaultVals) {
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
    }
  })

type CaseFieldFormValues = z.infer<typeof caseFieldFormSchema>

interface EditCustomFieldDialogProps {
  open: boolean
  field: CaseFieldReadMinimal | null
  onOpenChange: (open: boolean) => void
}

const emptyDefaults: CaseFieldFormValues = {
  name: "",
  type: "TEXT",
  nullable: true,
  default: null,
  defaultMulti: [],
  options: [],
}

function getFormDefaults(field: CaseFieldReadMinimal): CaseFieldFormValues {
  const safeType = isSqlTypeCreatable(field.type) ? field.type : "TEXT"
  const sanitizedOptions = sanitizeColumnOptions(field.options ?? [])

  if (safeType === "MULTI_SELECT") {
    return {
      name: field.id,
      type: safeType,
      nullable: field.nullable,
      default: "",
      defaultMulti: parseMultiSelectDefault(field.default),
      options: sanitizedOptions,
    }
  }

  return {
    name: field.id,
    type: safeType,
    nullable: field.nullable,
    default: field.default ?? "",
    defaultMulti: [],
    options: sanitizedOptions,
  }
}

export function EditCustomFieldDialog({
  open,
  field,
  onOpenChange,
}: EditCustomFieldDialogProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const previousTypeRef = useRef<SqlTypeCreatable | null>(null)

  const form = useForm<CaseFieldFormValues>({
    resolver: zodResolver(caseFieldFormSchema),
    defaultValues: emptyDefaults,
  })

  const selectedType = form.watch("type")
  const requiresOptions = isSelectableColumnType(selectedType)

  const selectableTypes = useMemo<SqlTypeCreatable[]>(() => {
    const baseTypes = SqlTypeCreatableEnum.filter((type) => type !== "JSONB")
    if (selectedType === "JSONB") {
      return [...baseTypes, "JSONB"]
    }
    return baseTypes
  }, [selectedType])

  useEffect(() => {
    if (!field || !open) {
      return
    }
    const defaults = getFormDefaults(field)
    form.reset(defaults)
    previousTypeRef.current = defaults.type
  }, [field, form, open])

  useEffect(() => {
    if (!open) {
      return
    }
    const previousType = previousTypeRef.current
    if (!previousType || previousType === selectedType) {
      previousTypeRef.current = selectedType
      return
    }

    form.setValue("default", "")
    form.setValue("defaultMulti", [])
    form.clearErrors("default")
    form.clearErrors("defaultMulti")

    if (!isSelectableColumnType(selectedType)) {
      form.setValue("options", [])
      form.clearErrors("options")
    }
    previousTypeRef.current = selectedType
  }, [form, open, selectedType])

  const onSubmit = async (data: CaseFieldFormValues) => {
    if (!field) return

    setIsSubmitting(true)
    try {
      let defaultValue: string | number | boolean | string[] | null = null
      const rawDefault = data.default

      if (data.type === "MULTI_SELECT") {
        const multiDefaults = data.defaultMulti || []
        if (multiDefaults.length > 0) {
          defaultValue = multiDefaults
        }
      } else if (
        rawDefault !== null &&
        rawDefault !== undefined &&
        rawDefault !== ""
      ) {
        switch (data.type) {
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
          case "TIMESTAMPTZ": {
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
          name: data.name,
          type: data.type,
          nullable: data.nullable,
          default: defaultValue,
          options: isSelectableColumnType(data.type)
            ? sanitizeColumnOptions(data.options)
            : null,
        },
      })

      queryClient.invalidateQueries({
        queryKey: ["case-fields", workspaceId],
      })

      toast({
        title: "Field updated",
        description: "The case field was updated successfully.",
      })

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
          return
        }
      }
      toast({
        title: "Error updating field",
        description: "Failed to update the case field. Please try again.",
        variant: "destructive",
      })
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
            Update this custom field for cases.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field: fieldInput }) => (
                <FormItem>
                  <FormLabel>Identifier / Slug</FormLabel>
                  <FormControl>
                    <Input {...fieldInput} />
                  </FormControl>
                  <FormDescription>
                    A human readable ID of the field. Use snake_case for best
                    compatibility.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="type"
              render={({ field: fieldInput }) => (
                <FormItem>
                  <FormLabel>Data type</FormLabel>
                  <Select
                    onValueChange={fieldInput.onChange}
                    value={fieldInput.value}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a data type" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {selectableTypes.map((type) => (
                        <SelectItem key={type} value={type}>
                          <SqlTypeDisplay
                            type={type}
                            labelClassName="text-xs"
                          />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    The SQL data type for this field.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {requiresOptions && (
              <FormField
                control={form.control}
                name="options"
                render={({ field: fieldInput }) => (
                  <FormItem>
                    <FormLabel>Options</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={fieldInput.value || []}
                        onChange={fieldInput.onChange}
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

            {selectedType === "MULTI_SELECT" ? (
              <FormField
                control={form.control}
                name="defaultMulti"
                render={({ field: fieldInput }) => (
                  <FormItem>
                    <FormLabel>Default values (optional)</FormLabel>
                    <FormControl>
                      <MultiTagCommandInput
                        value={fieldInput.value || []}
                        onChange={fieldInput.onChange}
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
                render={({ field: fieldInput }) => (
                  <FormItem>
                    <FormLabel>Default value (optional)</FormLabel>
                    <FormControl>
                      <DefaultValueInput
                        type={selectedType}
                        field={fieldInput}
                        options={form.watch("options")}
                      />
                    </FormControl>
                    <FormDescription>
                      {getDefaultHelperText(selectedType)}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                Save changes
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function getDefaultHelperText(type: SqlTypeCreatable | undefined) {
  switch (type) {
    case "INTEGER":
      return "Optional whole number that fills in missing values."
    case "NUMERIC":
      return "Optional numeric value (decimals allowed) used when none is provided."
    case "BOOLEAN":
      return "Accepts true, false, 1, or 0. Leave blank to omit a default."
    case "TIMESTAMPTZ":
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
  type: SqlTypeCreatable | undefined
  field: ControllerRenderProps<CaseFieldFormValues, "default">
  options?: string[]
}) {
  const resolvedType: SqlTypeCreatable = type ?? "TEXT"
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
    case "TIMESTAMPTZ": {
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
          value={field.value ?? ""}
          onValueChange={(value) =>
            field.onChange(value === "__none__" ? "" : value)
          }
        >
          <SelectTrigger>
            <SelectValue placeholder="Select a default value" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__">No default</SelectItem>
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
