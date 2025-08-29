"use client"

import { Loader2, RefreshCw } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { type Control, type FieldValues, useForm } from "react-hook-form"
import type { EntityFieldRead, EntityRead } from "@/client"
import {
  YamlStyledEditor,
  type YamlStyledEditorRef,
} from "@/components/editor/codemirror/yaml-editor"
import { JsonViewWithControls } from "@/components/json-viewer"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useEntities, useEntityFields } from "@/hooks/use-entities"
import { useCreateRecord } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { WorkflowProvider } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CreateRecordDialogProps {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  workspaceId?: string
  defaultEntityId?: string
  onSuccess?: () => void
}

interface FormData {
  entityId: string
  data: Record<string, unknown>
}

export function CreateRecordDialog({
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  workspaceId: propWorkspaceId,
  defaultEntityId,
  onSuccess,
}: CreateRecordDialogProps) {
  const workspaceId = propWorkspaceId ?? useWorkspaceId()
  const { entities } = useEntities(workspaceId)
  const { createRecord, createRecordIsPending } = useCreateRecord()

  const [internalOpen, setInternalOpen] = useState(false)

  const open = controlledOpen !== undefined ? controlledOpen : internalOpen
  const onOpenChange = controlledOnOpenChange || setInternalOpen

  const form = useForm<FormData>({
    defaultValues: {
      entityId: defaultEntityId || "",
      data: {},
    },
  })

  const yamlEditorRef = useRef<YamlStyledEditorRef | null>(null)

  const selectedEntityId = form.watch("entityId")
  const { fields, fieldsIsLoading, refetchFields } = useEntityFields(
    workspaceId,
    selectedEntityId,
    false
  )

  // Sample values for placeholders
  const placeholderForField = (f: EntityFieldRead): unknown => {
    const t = String(f.type).toUpperCase()
    if (t === "SELECT") {
      const options = (f.options || []).map((o) => o.key)
      return options[0] ?? "option"
    }
    if (t === "MULTI_SELECT") {
      const options = (f.options || []).map((o) => o.key)
      return options.length > 0
        ? options.slice(0, Math.max(1, Math.min(2, options.length)))
        : ["item"]
    }
    switch (t) {
      case "TEXT":
        return "text"
      case "INTEGER":
        return 123
      case "NUMBER":
        return 123.45
      case "BOOL":
        return true
      case "DATE":
        return "2025-01-01"
      case "DATETIME":
        return "2025-01-01T12:00:00Z"
      case "JSON":
        return { key: "value" }
      default:
        return "value"
    }
  }

  // Build example payload from field schema
  const examplePayload = useMemo(() => {
    if (!fields) return {}
    const ex: Record<string, unknown> = {}
    for (const f of fields as EntityFieldRead[]) {
      ex[f.key] = placeholderForField(f)
    }
    return ex
  }, [fields])

  // Prefill YAML editor with example payload when entity changes and dialog opens
  useEffect(() => {
    if (!open || !selectedEntityId) return
    // Only set if current data is empty to avoid clobbering user input
    const current = form.getValues("data")
    const isEmpty =
      !current ||
      (Object.keys(current).length === 0 && current.constructor === Object)
    if (isEmpty) {
      form.setValue("data", examplePayload, { shouldDirty: false })
    }
  }, [open, selectedEntityId, examplePayload, form])

  const onSubmit = async (values: FormData) => {
    try {
      // Ensure latest buffer is committed from YAML editor to form
      yamlEditorRef.current?.commitToForm()

      const recordData = values.data
      if (
        recordData === null ||
        typeof recordData !== "object" ||
        Array.isArray(recordData)
      ) {
        throw new Error("Invalid YAML: expected an object")
      }
      await createRecord({
        workspaceId,
        entityId: values.entityId,
        data: recordData,
      })
      form.reset()
      onOpenChange(false)
      onSuccess?.()
    } catch (_error) {
      form.setError("data", {
        type: "manual",
        message: "Invalid YAML format",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Create entity record</DialogTitle>
          <DialogDescription>
            Write the record payload as YAML.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="entityId"
              rules={{ required: "Please select an entity" }}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Entity</FormLabel>
                  <Select
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select an entity" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {entities?.map((entity: EntityRead) => (
                        <SelectItem key={entity.id} value={entity.id}>
                          {entity.display_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Schemas accordion with JSON viewer and controls */}
            <Accordion type="single" collapsible className="mb-3">
              <AccordionItem value="schemas">
                <div className="flex items-center justify-between pr-1">
                  <AccordionTrigger className="px-0 text-xs">
                    Schemas
                  </AccordionTrigger>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className={cn(
                            "text-muted-foreground hover:text-foreground",
                            fieldsIsLoading && "animate-spin"
                          )}
                          onClick={(e) => {
                            e.stopPropagation()
                            if (!selectedEntityId) return
                            refetchFields().then((result) => {
                              if (result.data) {
                                // Rebuild example payload from fresh fields
                                const freshPayload: Record<string, unknown> = {}
                                for (const f of result.data as EntityFieldRead[]) {
                                  freshPayload[f.key] = placeholderForField(f)
                                }
                                form.setValue("data", freshPayload, {
                                  shouldDirty: true,
                                })
                              }
                            })
                          }}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>Refresh schema</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
                <AccordionContent>
                  {selectedEntityId ? (
                    <div className="mb-3">
                      <JsonViewWithControls
                        src={examplePayload}
                        defaultExpanded
                        defaultTab="nested"
                        showControls={false}
                      />
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      Select an entity to view its schema.
                    </div>
                  )}
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            <FormField
              control={form.control}
              name="data"
              rules={{ required: "Please enter record data" }}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Record data</FormLabel>
                  <FormControl>
                    <div className="min-h-[200px]">
                      <WorkflowProvider workflowId="" workspaceId={workspaceId}>
                        <YamlStyledEditor
                          ref={yamlEditorRef}
                          name={"data"}
                          control={
                            form.control as unknown as Control<FieldValues>
                          }
                        />
                      </WorkflowProvider>
                    </div>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </form>
        </Form>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={createRecordIsPending}
          >
            Cancel
          </Button>
          <Button
            onClick={form.handleSubmit(onSubmit)}
            disabled={createRecordIsPending}
          >
            {createRecordIsPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            Create record
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
