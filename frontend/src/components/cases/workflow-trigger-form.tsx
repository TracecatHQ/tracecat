"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { PlayIcon, Plus, RotateCcw } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { JsonViewWithControls } from "@/components/json-viewer"
import {
  AlertDialogCancel,
  AlertDialogFooter,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
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
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { jsonSchemaToZod } from "@/lib/jsonschema"
import type { TracecatJsonSchema } from "@/lib/schema"

export type TriggerFormValues = Record<string, unknown>

interface WorkflowTriggerFormProps {
  schema: TracecatJsonSchema
  caseId: string
  caseFields: Record<string, unknown>
  groupCaseFields: boolean
  defaultTriggerValues?: Record<string, unknown> | null
  taskId?: string
  onSubmit: (values: TriggerFormValues) => Promise<void>
  isSubmitting: boolean
}

const JSON_INDENT = 2

const serializeEnumValue = (value: unknown): string => JSON.stringify(value)

const deserializeEnumValue = (value: string): unknown => {
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

const formatLabel = (key: string): string =>
  key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase())

const areValuesEqual = (a: unknown, b: unknown): boolean => {
  if (a === b) {
    return true
  }
  if (
    (typeof a === "object" && a !== null) ||
    (typeof b === "object" && b !== null)
  ) {
    try {
      return JSON.stringify(a) === JSON.stringify(b)
    } catch {
      return false
    }
  }
  return false
}

const isNullish = (value: unknown): value is null | undefined =>
  value === null || value === undefined

const formatValuePreview = (value: unknown): string => {
  if (value === undefined) {
    return ""
  }
  if (value === null) {
    return "null"
  }
  if (typeof value === "object") {
    try {
      const serialized = JSON.stringify(value)
      return serialized.length > 48
        ? `${serialized.slice(0, 45)}...`
        : serialized
    } catch {
      return "[object]"
    }
  }
  const stringified = String(value)
  return stringified.length > 48
    ? `${stringified.slice(0, 45)}...`
    : stringified
}

const getPrimaryType = (schema: TracecatJsonSchema): string | undefined => {
  if (!schema.type) {
    return undefined
  }
  return Array.isArray(schema.type) ? schema.type[0] : schema.type
}

const isValueCompatible = (
  schema: TracecatJsonSchema,
  value: unknown
): boolean => {
  if (isNullish(value)) {
    return true
  }

  const typeCandidates = schema.type
    ? Array.isArray(schema.type)
      ? schema.type
      : [schema.type]
    : []

  if (typeCandidates.length === 0) {
    return true
  }

  return typeCandidates.some((type) => {
    switch (type) {
      case "string":
        return typeof value === "string"
      case "number":
        return typeof value === "number" && Number.isFinite(value)
      case "integer":
        return (
          typeof value === "number" &&
          Number.isInteger(value) &&
          Number.isFinite(value)
        )
      case "boolean":
        return typeof value === "boolean"
      case "array":
        return Array.isArray(value)
      case "object":
        return (
          typeof value === "object" && value !== null && !Array.isArray(value)
        )
      case "null":
        return value === null
      default:
        return true
    }
  })
}

export function WorkflowTriggerForm({
  schema,
  caseId,
  caseFields,
  groupCaseFields,
  defaultTriggerValues,
  taskId,
  onSubmit,
  isSubmitting,
}: WorkflowTriggerFormProps) {
  const zodSchema = useMemo(() => {
    try {
      return jsonSchemaToZod(schema)
    } catch (error) {
      console.warn("Failed to convert workflow trigger schema", error)
      return null
    }
  }, [schema])

  const form = useForm<TriggerFormValues>({
    resolver: zodSchema ? zodResolver(zodSchema) : undefined,
    defaultValues: {},
  })

  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({})

  const buildJsonDrafts = useCallback(
    (values: TriggerFormValues) => {
      const drafts: Record<string, string> = {}
      Object.entries(schema.properties ?? {}).forEach(([key, definition]) => {
        if (typeof definition === "boolean") {
          return
        }
        const primaryType = getPrimaryType(definition)
        if (primaryType === "object" || primaryType === "array") {
          const value = values[key]
          drafts[key] =
            value !== undefined ? JSON.stringify(value, null, JSON_INDENT) : ""
        }
      })
      return drafts
    },
    [schema]
  )

  const computedDefaults = useMemo(() => {
    if (schema.type !== "object") {
      return {}
    }

    const defaults: TriggerFormValues = {}
    const properties = schema.properties ?? {}

    for (const [key, definition] of Object.entries(properties)) {
      if (typeof definition === "boolean") {
        continue
      }

      if (definition.default !== undefined) {
        defaults[key] = definition.default
      }

      if (key === "case_id") {
        defaults[key] = caseId
        continue
      }

      if (key === "task_id" && taskId) {
        defaults[key] = taskId
        continue
      }

      if (groupCaseFields && key === "case_fields") {
        defaults[key] = caseFields
        continue
      }

      if (!groupCaseFields && key in caseFields) {
        const caseFieldValue = caseFields[key]
        if (!isNullish(caseFieldValue)) {
          defaults[key] = caseFieldValue
        }
      }
    }

    if (defaultTriggerValues) {
      for (const [key, value] of Object.entries(defaultTriggerValues)) {
        if (value !== undefined) {
          defaults[key] = value
        }
      }
    }

    return defaults
  }, [
    schema,
    caseId,
    caseFields,
    groupCaseFields,
    defaultTriggerValues,
    taskId,
  ])

  useEffect(() => {
    form.reset(computedDefaults)
    setJsonDrafts(buildJsonDrafts(computedDefaults))
  }, [buildJsonDrafts, computedDefaults, form])

  const resetToDefaults = useCallback(() => {
    form.reset(computedDefaults)
    setJsonDrafts(buildJsonDrafts(computedDefaults))
  }, [buildJsonDrafts, computedDefaults, form])

  const requiredFields = useMemo(() => new Set(schema.required ?? []), [schema])

  const sanitizeInputs = useCallback(
    (values: TriggerFormValues) => {
      return Object.fromEntries(
        Object.entries(values ?? {}).filter(([key, value]) => {
          if (value === undefined) {
            return false
          }

          if (value === null && !requiredFields.has(key)) {
            return false
          }

          return true
        })
      )
    },
    [requiredFields]
  )

  const handleSubmit = useCallback(
    async (values: TriggerFormValues) => {
      const sanitized = sanitizeInputs(values)
      await onSubmit(sanitized)
    },
    [onSubmit, sanitizeInputs]
  )

  const watchedValues = form.watch()
  const previewValues = useMemo(
    () => sanitizeInputs(watchedValues),
    [sanitizeInputs, watchedValues]
  )

  const properties = useMemo(
    () =>
      Object.entries(schema.properties ?? {}).filter(
        (entry): entry is [string, TracecatJsonSchema] =>
          typeof entry[1] !== "boolean"
      ),
    [schema]
  )

  const fieldStatuses = useMemo(() => {
    const statusMap = new Map<string, MappingStatus>()

    properties.forEach(([fieldName, fieldSchema]) => {
      const currentValue = previewValues[fieldName]
      const defaultValue = computedDefaults[fieldName]
      const caseFieldValue =
        !groupCaseFields && fieldName in caseFields
          ? caseFields[fieldName]
          : undefined

      const matchesCaseId =
        fieldName === "case_id" && areValuesEqual(currentValue, caseId)
      const matchesCaseFields =
        (!groupCaseFields &&
          !isNullish(caseFieldValue) &&
          !isNullish(currentValue) &&
          areValuesEqual(currentValue, caseFieldValue)) ||
        (groupCaseFields &&
          fieldName === "case_fields" &&
          !isNullish(currentValue) &&
          areValuesEqual(currentValue, caseFields))

      const matchesDefault =
        !matchesCaseId &&
        !matchesCaseFields &&
        !isNullish(defaultValue) &&
        !isNullish(currentValue) &&
        areValuesEqual(currentValue, defaultValue)

      const status: MappingStatus =
        currentValue === undefined || currentValue === null
          ? "empty"
          : matchesCaseId || matchesCaseFields
            ? "case"
            : matchesDefault
              ? "schema-default"
              : "custom"

      statusMap.set(fieldName, status)
    })

    return statusMap
  }, [
    caseFields,
    caseId,
    computedDefaults,
    groupCaseFields,
    previewValues,
    properties,
  ])

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(handleSubmit)}
        className="mt-4 space-y-4"
      >
        <div className="flex flex-col gap-4">
          {properties.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              This workflow does not define any inputs. You can still trigger it
              with the current case context.
            </p>
          ) : (
            properties.map(([fieldName, fieldSchema]) => (
              <FormField
                key={fieldName}
                control={form.control}
                name={fieldName as keyof TriggerFormValues}
                render={({ field }) => {
                  const isRequired = requiredFields.has(fieldName)
                  const label = fieldSchema.title ?? formatLabel(fieldName)
                  const description = fieldSchema.description
                  const enumOptions = fieldSchema.enum
                  const fieldType = Array.isArray(fieldSchema.type)
                    ? fieldSchema.type[0]
                    : fieldSchema.type
                  const fieldTypeLabel = Array.isArray(fieldSchema.type)
                    ? fieldSchema.type.join(" | ")
                    : (fieldSchema.type ?? (enumOptions ? "enum" : undefined))
                  const fieldStatus = fieldStatuses.get(fieldName)

                  const statusBadge =
                    fieldStatus === "case" ? (
                      <TooltipProvider>
                        <Tooltip delayDuration={100}>
                          <TooltipTrigger asChild>
                            <span className="flex items-center gap-1 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">
                              Matches custom field
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="text-xs">
                            Auto-filled from case data
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : null

                  return (
                    <FormItem className="group space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <FormLabel className="flex items-center gap-2 text-xs font-medium">
                            <span className="flex items-center gap-1">
                              {label}
                              {isRequired && (
                                <span className="text-red-500">*</span>
                              )}
                            </span>
                            {statusBadge}
                            {fieldTypeLabel && (
                              <span className="font-mono text-[11px] text-muted-foreground opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
                                {fieldTypeLabel}
                              </span>
                            )}
                          </FormLabel>
                        </div>
                        <CaseValueSelector
                          fieldName={fieldName}
                          fieldSchema={fieldSchema}
                          caseFields={caseFields}
                          caseId={caseId}
                          enumOptions={enumOptions}
                          fieldType={fieldType}
                          onApply={(value) => {
                            field.onChange(value)
                          }}
                        />
                      </div>
                      <FormControl>
                        {enumOptions ? (
                          <Select
                            value={
                              field.value === undefined || field.value === null
                                ? undefined
                                : serializeEnumValue(field.value)
                            }
                            onValueChange={(value) =>
                              field.onChange(deserializeEnumValue(value))
                            }
                          >
                            <SelectTrigger>
                              <SelectValue
                                placeholder={`Select ${label.toLowerCase()}...`}
                              />
                            </SelectTrigger>
                            <SelectContent>
                              {enumOptions.map((option) => (
                                <SelectItem
                                  key={serializeEnumValue(option)}
                                  value={serializeEnumValue(option)}
                                >
                                  {String(option)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        ) : fieldType === "boolean" ? (
                          <Switch
                            id={`field-${fieldName}`}
                            checked={Boolean(field.value)}
                            onCheckedChange={(value) => field.onChange(value)}
                          />
                        ) : fieldType === "number" ||
                          fieldType === "integer" ? (
                          <Input
                            type="number"
                            value={
                              field.value === undefined || field.value === null
                                ? ""
                                : String(field.value)
                            }
                            onChange={(event) => {
                              const value = event.target.value
                              field.onChange(
                                value === "" ? undefined : Number(value)
                              )
                            }}
                          />
                        ) : fieldType === "object" || fieldType === "array" ? (
                          <div className="space-y-1">
                            <CodeEditor
                              value={jsonDrafts[fieldName] ?? ""}
                              onChange={(value) => {
                                setJsonDrafts((prev) => ({
                                  ...prev,
                                  [fieldName]: value,
                                }))
                                // Try to parse and update in real-time
                                if (!value.trim()) {
                                  field.onChange(undefined)
                                  form.clearErrors(
                                    fieldName as keyof TriggerFormValues
                                  )
                                  return
                                }
                                try {
                                  const parsed = JSON.parse(value)
                                  field.onChange(parsed)
                                  form.clearErrors(
                                    fieldName as keyof TriggerFormValues
                                  )
                                } catch {
                                  form.setError(
                                    fieldName as keyof TriggerFormValues,
                                    {
                                      type: "manual",
                                      message: "Invalid JSON",
                                    }
                                  )
                                }
                              }}
                              language="json"
                              className="min-h-[100px]"
                            />
                          </div>
                        ) : (
                          <Input
                            value={
                              field.value === undefined || field.value === null
                                ? ""
                                : String(field.value)
                            }
                            onChange={(event) =>
                              field.onChange(event.target.value)
                            }
                          />
                        )}
                      </FormControl>
                      {description && (
                        <FormDescription className="text-[11px] text-muted-foreground">
                          {description}
                        </FormDescription>
                      )}
                      <FormMessage className="text-[11px]" />
                    </FormItem>
                  )
                }}
              />
            ))
          )}
        </div>

        <div>
          <TooltipProvider>
            <JsonViewWithControls
              src={previewValues}
              showControls={false}
              defaultTab="nested"
              defaultExpanded
            />
          </TooltipProvider>
        </div>

        <AlertDialogFooter>
          <div className="flex items-center gap-2 mr-auto">
            <TooltipProvider>
              <Tooltip delayDuration={100}>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-7 px-2 text-xs rounded-full text-muted-foreground hover:text-foreground"
                    onClick={resetToDefaults}
                  >
                    <RotateCcw className="size-3 mr-1" />
                    Reset inputs
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top" className="text-xs">
                  Reset to case defaults
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <AlertDialogCancel className="text-xs">Cancel</AlertDialogCancel>
          <Button type="submit" className="text-xs" disabled={isSubmitting}>
            <PlayIcon className="mr-1.5 h-3 w-3" />
            Trigger
          </Button>
        </AlertDialogFooter>
      </form>
    </Form>
  )
}

type MappingStatus = "case" | "schema-default" | "custom" | "empty"

interface CaseValueSelectorProps {
  fieldName: string
  fieldSchema: TracecatJsonSchema
  caseFields: Record<string, unknown>
  caseId: string
  enumOptions?: unknown[]
  fieldType?: string | null
  onApply: (value: unknown | undefined) => void
}

function CaseValueSelector({
  fieldName,
  fieldSchema,
  caseFields,
  caseId,
  enumOptions,
  fieldType,
  onApply,
}: CaseValueSelectorProps) {
  const [open, setOpen] = useState(false)

  const suggestions = useMemo(() => {
    if (enumOptions && enumOptions.length > 0) {
      return []
    }

    if (fieldType === "object" || fieldType === "array") {
      return []
    }

    const entries = Object.entries(caseFields)
    const results: Array<{
      id: string
      label: string
      value: unknown
      preview: string
    }> = []
    const addSuggestion = (id: string, label: string, value: unknown) => {
      if (!isValueCompatible(fieldSchema, value)) {
        return
      }
      if (
        fieldSchema.enum &&
        !fieldSchema.enum.some((option) => areValuesEqual(option, value))
      ) {
        return
      }
      if (results.some((item) => item.id === id)) {
        return
      }
      results.push({
        id,
        label,
        value,
        preview: formatValuePreview(value),
      })
    }

    const directMatch = entries.find(([key]) => key === fieldName)
    if (directMatch) {
      addSuggestion(
        `case-field-${directMatch[0]}`,
        `Use case field • ${formatLabel(directMatch[0])}`,
        directMatch[1]
      )
    }

    if (isValueCompatible(fieldSchema, caseId)) {
      addSuggestion("case-id", "Use case ID", caseId)
    }

    entries
      .filter(([key]) => key !== fieldName)
      .forEach(([key, value]) => {
        addSuggestion(
          `case-field-${key}`,
          `Case field • ${formatLabel(key)}`,
          value
        )
      })

    return results
  }, [caseFields, caseId, enumOptions, fieldName, fieldSchema, fieldType])

  if (suggestions.length === 0) {
    return null
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className="h-6 px-2 text-[11px] font-medium text-muted-foreground pointer-events-none opacity-0 transition-opacity duration-150 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100 data-[state=open]:pointer-events-auto data-[state=open]:opacity-100"
        >
          <Plus className="size-3 mr-1" />
          Add case value
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 p-0">
        <Command>
          <CommandInput placeholder="Search case values..." />
          <CommandList>
            <CommandEmpty>No matching case values</CommandEmpty>
            <CommandGroup heading="Suggestions">
              {suggestions.map((suggestion) => (
                <CommandItem
                  key={suggestion.id}
                  value={suggestion.id}
                  onSelect={() => {
                    onApply(suggestion.value)
                    setOpen(false)
                  }}
                  className="flex flex-col items-start gap-0.5 text-xs"
                >
                  <span className="font-medium">{suggestion.label}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {suggestion.preview}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
            <CommandGroup heading="Custom">
              <CommandItem
                value="custom-value"
                onSelect={() => {
                  onApply(undefined)
                  setOpen(false)
                }}
                className="text-xs"
              >
                Clear for custom input
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
