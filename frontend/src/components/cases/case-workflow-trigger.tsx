"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import fuzzysort from "fuzzysort"
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ChevronsUpDown,
  PlayIcon,
  Plus,
  RotateCcw,
} from "lucide-react"
import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import type { ApiError, CaseRead, WorkflowRead } from "@/client"
import { workflowsGetWorkflow } from "@/client"
import { JsonViewWithControls } from "@/components/json-viewer"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
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
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useLocalStorage } from "@/hooks/use-local-storage"
import {
  useCreateManualWorkflowExecution,
  useWorkflowManager,
} from "@/lib/hooks"
import { jsonSchemaToZod } from "@/lib/jsonschema"
import type { TracecatJsonSchema } from "@/lib/schema"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CaseWorkflowTriggerProps {
  caseData: CaseRead
}

type WorkflowWithSchema = WorkflowRead & {
  expects_schema?: TracecatJsonSchema | null
}

type TriggerFormValues = Record<string, unknown>

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

const getPrimaryType = (schema: TracecatJsonSchema): string | null => {
  if (!schema.type) {
    return null
  }
  return Array.isArray(schema.type) ? (schema.type[0] ?? null) : schema.type
}

const isValueCompatible = (
  schema: TracecatJsonSchema,
  value: unknown
): boolean => {
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

/**
 * Renders a workflow trigger section for a case.
 * Allows selecting a workflow and triggering it with the case data as input.
 * @param caseData The data of the current case.
 * @returns JSX.Element
 */
export function CaseWorkflowTrigger({ caseData }: CaseWorkflowTriggerProps) {
  const workspaceId = useWorkspaceId()
  // Get the manual execution hook for the selected workflow (if any)
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(
    null
  )
  const [searchTerm, setSearchTerm] = useState("")
  const [isComboboxOpen, setIsComboboxOpen] = useState(false)
  // Use the useLocalStorage hook
  const [groupCaseFields, setGroupCaseFields] = useLocalStorage(
    "groupCaseFields",
    false
  )

  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(selectedWorkflowId || "")
  const caseFieldsRecord = useMemo(
    () =>
      Object.fromEntries(
        caseData.fields
          .filter((field) => !field.reserved)
          .map((field) => [field.id, field.value])
      ),
    [caseData.fields]
  )
  const fallbackInputs = useMemo(() => {
    if (groupCaseFields) {
      return {
        case_id: caseData.id,
        case_fields: caseFieldsRecord,
      }
    }
    return {
      case_id: caseData.id,
      ...caseFieldsRecord,
    }
  }, [caseData.id, caseFieldsRecord, groupCaseFields])
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)

  const selectedWorkflowUrl = `/workspaces/${workspaceId}/workflows/${selectedWorkflowId}`
  // Fetch workflows
  const { workflows, workflowsLoading, workflowsError } = useWorkflowManager()
  const { data: selectedWorkflowDetail } = useQuery<
    WorkflowWithSchema | null,
    ApiError
  >({
    enabled: Boolean(selectedWorkflowId),
    queryKey: ["workflow-detail", selectedWorkflowId],
    queryFn: async ({ queryKey }) => {
      const workflowId = queryKey[1] as string | null
      if (!workflowId) {
        return null
      }
      const workflow = await workflowsGetWorkflow({
        workspaceId,
        workflowId,
      })
      return workflow as WorkflowWithSchema
    },
  })

  const searchableWorkflows = useMemo(
    () =>
      (workflows ?? []).map((workflow) => ({
        workflow,
        title: workflow.title,
        alias: workflow.alias ?? "",
      })),
    [workflows]
  )

  const filteredWorkflows = useMemo(() => {
    if (!searchableWorkflows.length) {
      return []
    }

    if (!searchTerm.trim()) {
      return searchableWorkflows
    }

    const results = fuzzysort.go(searchTerm, searchableWorkflows, {
      all: true,
      keys: ["title", "alias"],
    })

    return results.map((result) => result.obj)
  }, [searchableWorkflows, searchTerm])

  const triggerSchema = useMemo<TracecatJsonSchema | null>(() => {
    const schema = selectedWorkflowDetail?.expects_schema
    if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
      return null
    }
    if ("type" in schema && schema.type !== "object") {
      return null
    }
    return schema as TracecatJsonSchema
  }, [selectedWorkflowDetail])

  const effectiveGroupCaseFields = triggerSchema ? false : groupCaseFields

  const showExecutionStartedToast = useCallback(() => {
    if (!selectedWorkflowId) {
      return
    }
    toast({
      title: "Workflow run started",
      description: (
        <Link
          href={selectedWorkflowUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          <div className="flex items-center space-x-1">
            <ArrowUpRight className="size-3" />
            <span>View workflow run</span>
          </div>
        </Link>
      ),
    })
  }, [selectedWorkflowId, selectedWorkflowUrl])

  const handleSchemaSubmit = useCallback(
    async (values: TriggerFormValues) => {
      if (!selectedWorkflowId) return
      await createExecution({
        workflow_id: selectedWorkflowId,
        inputs: values,
      })
      showExecutionStartedToast()
      setIsConfirmOpen(false)
    },
    [createExecution, selectedWorkflowId, showExecutionStartedToast]
  )

  const handleTriggerWithoutSchema = useCallback(async () => {
    if (!selectedWorkflowId) return
    await createExecution({
      workflow_id: selectedWorkflowId,
      inputs: fallbackInputs,
    })
    showExecutionStartedToast()
    setIsConfirmOpen(false)
  }, [
    createExecution,
    fallbackInputs,
    selectedWorkflowId,
    showExecutionStartedToast,
  ])

  // Loading state
  if (workflowsLoading) {
    return <Skeleton className="h-8 w-full" />
  }

  // Error state
  if (workflowsError) {
    return (
      <div className="text-xs text-destructive">
        Error loading workflows: {workflowsError.message}
      </div>
    )
  }

  const selectedWorkflow = workflows?.find((wf) => wf.id === selectedWorkflowId)
  return (
    <div className="space-y-3">
      <Popover
        open={isComboboxOpen}
        onOpenChange={(open) => {
          setIsComboboxOpen(open)
          if (!open) {
            setSearchTerm("")
          }
        }}
      >
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            aria-expanded={isComboboxOpen}
            aria-haspopup="listbox"
            className="h-8 w-full justify-between border-muted text-xs"
          >
            <span className="flex min-w-0 items-center gap-2 truncate">
              {selectedWorkflow ? (
                <>
                  <span className="truncate">{selectedWorkflow.title}</span>
                  {selectedWorkflow.alias && (
                    <Badge
                      variant="secondary"
                      className="px-1 py-0 text-[10px] font-normal"
                    >
                      {selectedWorkflow.alias}
                    </Badge>
                  )}
                </>
              ) : (
                <span className="text-muted-foreground">
                  Select a workflow...
                </span>
              )}
            </span>
            <ChevronsUpDown className="ml-2 size-3 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          className="w-[--radix-popover-trigger-width] min-w-64 p-0"
          align="start"
        >
          <Command shouldFilter={false}>
            <CommandInput
              placeholder="Search workflows..."
              value={searchTerm}
              onValueChange={setSearchTerm}
            />
            <CommandList>
              {workflowsLoading ? (
                <CommandEmpty>Loading workflows...</CommandEmpty>
              ) : workflowsError ? (
                <CommandEmpty>Failed to load workflows</CommandEmpty>
              ) : filteredWorkflows.length === 0 ? (
                <CommandEmpty>No workflows found</CommandEmpty>
              ) : (
                <CommandGroup>
                  {filteredWorkflows.map(({ workflow }) => (
                    <CommandItem
                      key={workflow.id}
                      value={workflow.id}
                      onSelect={() => {
                        setSelectedWorkflowId(workflow.id)
                        setIsComboboxOpen(false)
                        setSearchTerm("")
                      }}
                      className="flex flex-col items-start py-2"
                    >
                      <div className="flex w-full items-center gap-2">
                        <span className="truncate font-medium">
                          {workflow.title}
                        </span>
                        {workflow.alias && (
                          <Badge
                            variant="secondary"
                            className="px-1 py-0 text-[10px] font-normal"
                          >
                            {workflow.alias}
                          </Badge>
                        )}
                      </div>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

      <AlertDialog open={isConfirmOpen} onOpenChange={setIsConfirmOpen}>
        <AlertDialogTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            disabled={!selectedWorkflowId || createExecutionIsPending}
            className="w-full h-8 text-xs"
          >
            <PlayIcon className="mr-1.5 h-3 w-3" />
            Trigger
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent className="max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-sm">
              {triggerSchema
                ? "Configure workflow inputs"
                : "Confirm workflow trigger"}
            </AlertDialogTitle>
            <AlertDialogDescription className="text-xs">
              {triggerSchema
                ? `Provide the inputs required by "${selectedWorkflow?.title ?? "this workflow"}". Defaults are populated from the case where possible.`
                : `Are you sure you want to trigger "${selectedWorkflow?.title ?? "this workflow"}" with the following inputs?`}
            </AlertDialogDescription>
          </AlertDialogHeader>

          {triggerSchema ? (
            <SchemaDrivenTriggerForm
              schema={triggerSchema}
              caseId={caseData.id}
              caseFields={caseFieldsRecord}
              groupCaseFields={effectiveGroupCaseFields}
              onSubmit={handleSchemaSubmit}
              isSubmitting={createExecutionIsPending}
            />
          ) : (
            <>
              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-muted/40 px-3 py-2">
                  <div className="space-y-1 text-xs">
                    <div className="font-medium">Group case fields</div>
                    <p className="text-[11px] text-muted-foreground">
                      Send case data under a single <code>case_fields</code>{" "}
                      object.
                    </p>
                  </div>
                  <Switch
                    checked={groupCaseFields}
                    onCheckedChange={(value) => setGroupCaseFields(value)}
                    className="h-4 w-8"
                  />
                </div>
                <TooltipProvider>
                  <JsonViewWithControls
                    src={fallbackInputs}
                    showControls={false}
                    defaultTab="nested"
                    defaultExpanded
                  />
                </TooltipProvider>
              </div>
              <AlertDialogFooter>
                <AlertDialogCancel className="text-xs">
                  Cancel
                </AlertDialogCancel>
                <Button
                  type="button"
                  onClick={handleTriggerWithoutSchema}
                  className="text-xs"
                  disabled={createExecutionIsPending}
                >
                  <PlayIcon className="mr-1.5 h-3 w-3" />
                  Trigger
                </Button>
              </AlertDialogFooter>
            </>
          )}
        </AlertDialogContent>
      </AlertDialog>

      {selectedWorkflowId && (
        <Link
          href={selectedWorkflowUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowUpRight className="h-3 w-3" />
          <span>View workflow</span>
        </Link>
      )}
    </div>
  )
}

interface SchemaDrivenTriggerFormProps {
  schema: TracecatJsonSchema
  caseId: string
  caseFields: Record<string, unknown>
  groupCaseFields: boolean
  onSubmit: (values: TriggerFormValues) => Promise<void>
  isSubmitting: boolean
}

function SchemaDrivenTriggerForm({
  schema,
  caseId,
  caseFields,
  groupCaseFields,
  onSubmit,
  isSubmitting,
}: SchemaDrivenTriggerFormProps) {
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

      if (groupCaseFields && key === "case_fields") {
        defaults[key] = caseFields
        continue
      }

      if (!groupCaseFields && key in caseFields) {
        defaults[key] = caseFields[key]
      }
    }

    return defaults
  }, [schema, caseId, caseFields, groupCaseFields])

  useEffect(() => {
    form.reset(computedDefaults)
    setJsonDrafts(buildJsonDrafts(computedDefaults))
  }, [buildJsonDrafts, computedDefaults, form])

  const resetToDefaults = useCallback(() => {
    form.reset(computedDefaults)
    setJsonDrafts(buildJsonDrafts(computedDefaults))
  }, [buildJsonDrafts, computedDefaults, form])

  const sanitizeInputs = useCallback((values: TriggerFormValues) => {
    return Object.fromEntries(
      Object.entries(values ?? {}).filter(([, value]) => value !== undefined)
    )
  }, [])

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
  const requiredFields = useMemo(() => new Set(schema.required ?? []), [schema])

  const mappingDetails: MappingDetail[] = useMemo(() => {
    return properties.map(([fieldName, fieldSchema]) => {
      const label = fieldSchema.title ?? formatLabel(fieldName)
      const currentValue = previewValues[fieldName]
      const defaultValue = computedDefaults[fieldName]
      const isRequired = requiredFields.has(fieldName)

      const matchesCaseId =
        fieldName === "case_id" && areValuesEqual(currentValue, caseId)
      const matchesCaseFields =
        (!groupCaseFields &&
          fieldName in caseFields &&
          areValuesEqual(currentValue, caseFields[fieldName])) ||
        (groupCaseFields &&
          fieldName === "case_fields" &&
          areValuesEqual(currentValue, caseFields))

      const matchesDefault =
        !matchesCaseId &&
        !matchesCaseFields &&
        defaultValue !== undefined &&
        areValuesEqual(currentValue, defaultValue)

      const status: MappingStatus =
        currentValue === undefined
          ? "empty"
          : matchesCaseId || matchesCaseFields
            ? "case"
            : matchesDefault
              ? "schema-default"
              : "custom"

      return {
        fieldName,
        label,
        required: isRequired,
        status,
        valuePreview:
          currentValue === undefined
            ? isRequired
              ? "Required"
              : "Optional"
            : formatValuePreview(currentValue),
      }
    })
  }, [
    caseFields,
    caseId,
    computedDefaults,
    groupCaseFields,
    previewValues,
    properties,
    requiredFields,
  ])

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(handleSubmit)}
        className="mt-4 space-y-4"
      >
        {mappingDetails.length > 0 && (
          <div className="flex w-full items-center justify-between gap-3 text-xs">
            <CaseMappingSummary details={mappingDetails} />
          </div>
        )}

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
                name={fieldName}
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

                  return (
                    <FormItem className="group space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <FormLabel className="flex items-center gap-2 text-xs font-medium">
                          <span className="flex items-center gap-1">
                            {label}
                            {isRequired && (
                              <span className="text-red-500">*</span>
                            )}
                          </span>
                          {fieldTypeLabel && (
                            <span className="font-mono text-[11px] text-muted-foreground opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
                              {fieldTypeLabel}
                            </span>
                          )}
                        </FormLabel>
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
                          <Textarea
                            value={jsonDrafts[fieldName] ?? ""}
                            onChange={(event) => {
                              const value = event.target.value
                              setJsonDrafts((prev) => ({
                                ...prev,
                                [fieldName]: value,
                              }))
                            }}
                            onBlur={() => {
                              const rawValue = jsonDrafts[fieldName] ?? ""
                              if (!rawValue.trim()) {
                                field.onChange(undefined)
                                form.clearErrors(
                                  fieldName as keyof TriggerFormValues
                                )
                                return
                              }
                              try {
                                const parsed = JSON.parse(rawValue)
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
                            className="font-mono text-xs"
                            rows={4}
                          />
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

interface MappingDetail {
  fieldName: string
  label: string
  required: boolean
  status: MappingStatus
  valuePreview: string
}

interface CaseMappingSummaryProps {
  details: MappingDetail[]
}

function CaseMappingSummary({ details }: CaseMappingSummaryProps) {
  if (details.length === 0) {
    return null
  }

  const autoMappedCount = details.filter(
    (detail) => detail.status === "case"
  ).length
  const customCount = details.filter(
    (detail) => detail.status === "custom"
  ).length
  const total = details.length
  const requiredPending = details.filter(
    (detail) => detail.required && detail.status === "empty"
  ).length

  const tone: "success" | "info" | "warning" =
    requiredPending > 0 ? "warning" : customCount > 0 ? "info" : "success"

  const toneClasses: Record<"success" | "info" | "warning", string> = {
    success:
      "border-emerald-200 bg-emerald-100/80 text-emerald-800 hover:bg-emerald-100",
    info: "border-blue-200 bg-blue-100/80 text-blue-800 hover:bg-blue-100",
    warning:
      "border-amber-200 bg-amber-100/80 text-amber-800 hover:bg-amber-100",
  }

  const Icon = tone === "warning" ? AlertTriangle : CheckCircle2

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium",
        toneClasses[tone]
      )}
    >
      <Icon className="size-3" />
      <span className="font-semibold">
        {autoMappedCount}/{total} inputs mapped
      </span>
      {tone === "warning" && (
        <span className="text-[10px] text-amber-700">
          • {requiredPending} required missing
        </span>
      )}
      {tone === "info" && (
        <span className="text-[10px] text-blue-700">
          • {customCount} custom {customCount === 1 ? "value" : "values"}
        </span>
      )}
    </div>
  )
}

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
