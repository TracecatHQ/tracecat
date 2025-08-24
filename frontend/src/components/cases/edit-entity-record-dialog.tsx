"use client"

import { CheckCheck, Copy, Loader2, RotateCcw } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import YAML from "yaml"
import type { CaseRecordLinkRead, EntitySchemaField } from "@/client"
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
import { Form } from "@/components/ui/form"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useGetEntitySchema, useUpdateCaseRecord } from "@/lib/hooks"

interface EditEntityRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  recordLink: CaseRecordLinkRead
  workspaceId: string
  onSuccess?: () => void
}

export function EditEntityRecordDialog({
  open,
  onOpenChange,
  caseId,
  recordLink,
  workspaceId,
  onSuccess,
}: EditEntityRecordDialogProps) {
  const entityId = recordLink.record?.entity_id || recordLink.entity_id
  const { schema, isLoading: schemaLoading } = useGetEntitySchema({
    entityId,
    workspaceId,
  })

  const { updateRecord, isUpdating } = useUpdateCaseRecord({
    caseId,
    recordId: recordLink.record?.id || recordLink.record_id,
    workspaceId,
  })

  const form = useForm<{ record_data: Record<string, unknown> | undefined }>({
    defaultValues: { record_data: recordLink.record?.field_data || {} },
  })
  const editorRef = useRef<YamlStyledEditorRef | null>(null)
  const [copied, setCopied] = useState(false)
  const [relationSchemas, setRelationSchemas] = useState<
    Map<string, EntitySchemaField[]>
  >(new Map())

  useEffect(() => {
    form.reset({ record_data: recordLink.record?.field_data || {} })
  }, [recordLink.record?.field_data])

  // Ensure prefill each time dialog opens
  useEffect(() => {
    if (open) {
      form.reset({ record_data: recordLink.record?.field_data || {} })
    }
  }, [open])

  const isLoadingData = schemaLoading
  const hasFillableFields = useMemo(() => true, [])
  // Shared helpers to build placeholder values
  const placeholderForType = (t: string): unknown => {
    const type = t.toUpperCase()
    switch (type) {
      case "TEXT":
      case "STRING":
      case "LONGTEXT":
      case "TEXTAREA":
        return "text"
      case "INTEGER":
      case "INT":
        return 123
      case "NUMBER":
      case "FLOAT":
      case "DECIMAL":
        return 123.45
      case "BOOL":
      case "BOOLEAN":
        return true
      case "DATE":
        return "2025-01-01"
      case "DATETIME":
      case "TIMESTAMP":
        return "2025-01-01T12:00:00Z"
      case "JSON":
      case "OBJECT":
        return { key: "value" }
      default:
        return "value"
    }
  }

  const placeholderForField = (f: EntitySchemaField): unknown => {
    const t = f.type.toUpperCase()
    if (t === "SELECT" || t === "ENUM") {
      if (f.enum_options && f.enum_options.length > 0) return f.enum_options[0]
      return "option"
    }
    if (t === "MULTI_SELECT" || t === "MULTISELECT") {
      if (f.enum_options && f.enum_options.length > 0) {
        return f.enum_options.slice(
          0,
          Math.max(1, Math.min(2, f.enum_options.length))
        )
      }
      return ["item"]
    }
    if (t.startsWith("ARRAY_")) {
      const itemType = t.replace("ARRAY_", "")
      const sample = placeholderForType(itemType)
      return [sample, sample]
    }
    return placeholderForType(t)
  }
  const examplePayload = useMemo(() => {
    if (!schema) return {}
    const ex: Record<string, unknown> = {}

    for (const f of schema.fields) {
      ex[f.key] = placeholderForField(f)
    }
    for (const r of schema.relations || []) {
      const relType = String(r.relation_type)
      const fields = relationSchemas.get(r.source_key)
      const buildObj = () => {
        const obj: Record<string, unknown> = {}
        if (fields && fields.length > 0) {
          for (const f of fields) {
            obj[f.key] = placeholderForField(f)
          }
        } else {
          obj["key"] = "value"
        }
        return obj
      }
      if (relType === "one_to_one" || relType === "many_to_one") {
        ex[r.source_key] = buildObj()
      } else {
        ex[r.source_key] = [buildObj()]
      }
    }
    return ex
  }, [schema, relationSchemas])

  // Fetch relation target schemas to show per-relation examples
  useEffect(() => {
    const fetchSchemas = async () => {
      if (!schema?.relations || schema.relations.length === 0) {
        setRelationSchemas(new Map())
        return
      }
      const m = new Map<string, EntitySchemaField[]>()
      try {
        const { entitiesGetEntitySchema } = await import("@/client")
        for (const r of schema.relations) {
          const target = await entitiesGetEntitySchema({
            entityId: r.target_entity_id,
            workspaceId,
          })
          const fields = (target.fields || []).filter(
            (f) => !f.type.startsWith("RELATION_")
          )
          m.set(r.source_key, fields)
        }
      } catch {}
      setRelationSchemas(m)
    }
    fetchSchemas()
  }, [schema?.relations, workspaceId])

  const relationExamples = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    relationSchemas.forEach((fields, key) => {
      const obj: Record<string, unknown> = {}
      fields.forEach((f) => {
        obj[f.key] = placeholderForField(f)
      })
      map.set(key, obj)
    })
    return map
  }, [relationSchemas])

  const handleSubmit = async (values: {
    record_data?: Record<string, unknown>
  }) => {
    try {
      await updateRecord(
        (values.record_data || {}) as import("@/client").RecordUpdate
      )
      onOpenChange(false)
      onSuccess?.()
    } catch (error) {
      console.error("Failed to update entity record:", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Edit entity record</DialogTitle>
          <DialogDescription>
            Edit the record payload as YAML. Include relation keys to create or
            update related records.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-1 py-0.5">
          <Accordion type="single" collapsible className="mb-3">
            <AccordionItem value="schemas">
              <div className="flex items-center justify-between pr-1">
                <AccordionTrigger className="px-0 text-xs">
                  Schemas
                </AccordionTrigger>
                <div className="flex items-center gap-1">
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className="mr-1 text-muted-foreground hover:text-foreground"
                          onClick={(e) => {
                            e.stopPropagation()
                            form.setValue(
                              "record_data",
                              (recordLink.record?.field_data || {}) as Record<
                                string,
                                unknown
                              >,
                              { shouldDirty: true }
                            )
                          }}
                        >
                          <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>Reset to current values</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className="mr-1 text-muted-foreground hover:text-foreground"
                          onClick={async (e) => {
                            e.stopPropagation()
                            try {
                              const yamlText = YAML.stringify(examplePayload, {
                                lineWidth: 0,
                                minContentWidth: 0,
                              })
                              await navigator.clipboard?.writeText(yamlText)
                            } catch {}
                            form.setValue("record_data", examplePayload, {
                              shouldDirty: true,
                            })
                            setCopied(true)
                            setTimeout(() => setCopied(false), 1200)
                          }}
                        >
                          {copied ? (
                            <CheckCheck className="h-3.5 w-3.5" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>Copy sample payload</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
              </div>
              <AccordionContent>
                <div className="mb-3">
                  <JsonViewWithControls
                    src={examplePayload}
                    defaultExpanded
                    defaultTab="nested"
                    showControls={false}
                  />
                </div>
                {relationExamples.size > 0 && (
                  <div className="mb-3 space-y-2">
                    {Array.from(relationExamples.entries()).map(
                      ([key, obj]) => (
                        <div key={key} className="space-y-1">
                          <div className="text-[11px] text-muted-foreground">
                            {key}
                          </div>
                          <JsonViewWithControls
                            src={obj}
                            defaultExpanded
                            defaultTab="nested"
                            showControls={false}
                          />
                        </div>
                      )
                    )}
                  </div>
                )}
              </AccordionContent>
            </AccordionItem>
          </Accordion>
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(handleSubmit)}
              className="space-y-4"
            >
              <YamlStyledEditor
                ref={editorRef}
                name="record_data"
                control={form.control}
              />
            </form>
          </Form>
        </div>

        <DialogFooter className="pt-4">
          <div className="flex items-center justify-end w-full">
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  onOpenChange(false)
                  form.reset()
                }}
                disabled={isUpdating}
              >
                Cancel
              </Button>
              <Button
                onClick={() => {
                  editorRef.current?.commitToForm()
                  form.handleSubmit(handleSubmit)()
                }}
                disabled={
                  !entityId ||
                  isUpdating ||
                  isLoadingData ||
                  (schema && !hasFillableFields)
                }
              >
                {isUpdating && (
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                )}
                Save changes
              </Button>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
