"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import fuzzysort from "fuzzysort"
import { ArrowUpRight, ChevronsUpDown, PlayIcon } from "lucide-react"
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
import { Label } from "@/components/ui/label"
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
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useLocalStorage } from "@/hooks/use-local-storage"
import {
  useCreateManualWorkflowExecution,
  useWorkflowManager,
} from "@/lib/hooks"
import { jsonSchemaToZod } from "@/lib/jsonschema"
import type { TracecatJsonSchema } from "@/lib/schema"
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
            role="combobox"
            aria-expanded={isComboboxOpen}
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
              groupCaseFields={groupCaseFields}
              onGroupCaseFieldsChange={setGroupCaseFields}
              onSubmit={handleSchemaSubmit}
              isSubmitting={createExecutionIsPending}
            />
          ) : (
            <>
              <div className="mt-4">
                <TooltipProvider>
                  <JsonViewWithControls
                    src={fallbackInputs}
                    showControls={false}
                    defaultTab="nested"
                    defaultExpanded
                  />
                </TooltipProvider>

                <div className="mt-4 flex items-center space-x-2">
                  <Switch
                    id="group-fields"
                    checked={groupCaseFields}
                    onCheckedChange={setGroupCaseFields}
                    className="h-4 w-8"
                  />
                  <Label htmlFor="group-fields" className="text-xs">
                    Group case fields
                  </Label>
                </div>
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
  onGroupCaseFieldsChange: (value: boolean) => void
  onSubmit: (values: TriggerFormValues) => Promise<void>
  isSubmitting: boolean
}

function SchemaDrivenTriggerForm({
  schema,
  caseId,
  caseFields,
  groupCaseFields,
  onGroupCaseFieldsChange,
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
        continue
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

    const nextDrafts: Record<string, string> = {}
    Object.entries(schema.properties ?? {}).forEach(([key, definition]) => {
      if (typeof definition === "boolean") {
        return
      }
      if (definition.type === "object" || definition.type === "array") {
        const value = computedDefaults[key]
        nextDrafts[key] =
          value !== undefined ? JSON.stringify(value, null, JSON_INDENT) : ""
      }
    })
    setJsonDrafts(nextDrafts)
  }, [computedDefaults, form, schema])

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
                name={fieldName}
                render={({ field }) => {
                  const isRequired = requiredFields.has(fieldName)
                  const label = fieldSchema.title ?? formatLabel(fieldName)
                  const description = fieldSchema.description
                  const enumOptions = fieldSchema.enum
                  const fieldType = Array.isArray(fieldSchema.type)
                    ? fieldSchema.type[0]
                    : fieldSchema.type

                  return (
                    <FormItem className="space-y-2">
                      <FormLabel className="text-xs font-medium">
                        {label}
                        {isRequired && (
                          <span className="ml-1 text-red-500">*</span>
                        )}
                      </FormLabel>
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

        <div className="flex items-center space-x-2">
          <Switch
            id="group-fields-schema"
            checked={groupCaseFields}
            onCheckedChange={onGroupCaseFieldsChange}
            className="h-4 w-8"
          />
          <Label htmlFor="group-fields-schema" className="text-xs">
            Group case fields
          </Label>
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
