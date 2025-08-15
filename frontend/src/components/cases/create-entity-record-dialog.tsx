"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseRecordLinkCreate, EntitySchemaField } from "@/client"
import { EntityFieldInput } from "@/components/cases/entity-field-input"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useCreateCaseRecord,
  useGetEntitySchema,
  useListEntities,
} from "@/lib/hooks"

interface CreateEntityRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  workspaceId: string
  onSuccess?: () => void
}

function generateZodSchema(
  fields: EntitySchemaField[]
): z.ZodObject<Record<string, z.ZodTypeAny>> {
  const schema: Record<string, z.ZodTypeAny> = {}

  fields.forEach((field) => {
    const fieldType = field.type.toUpperCase()
    let fieldSchema: z.ZodTypeAny

    switch (fieldType) {
      case "TEXT":
      case "STRING":
      case "LONGTEXT":
      case "TEXTAREA":
        fieldSchema = z.string().nullable().optional()
        break
      case "INTEGER":
      case "INT":
        fieldSchema = z.number().int().nullable().optional()
        break
      case "NUMBER":
      case "FLOAT":
      case "DECIMAL":
        fieldSchema = z.number().nullable().optional()
        break
      case "BOOL":
      case "BOOLEAN":
        fieldSchema = z.boolean().default(false)
        break
      case "DATE":
        fieldSchema = z.string().nullable().optional()
        break
      case "DATETIME":
      case "TIMESTAMP":
        fieldSchema = z.string().nullable().optional()
        break
      case "SELECT":
      case "ENUM":
        if (field.enum_options && field.enum_options.length > 0) {
          fieldSchema = z
            .enum(field.enum_options as [string, ...string[]])
            .nullable()
            .optional()
        } else {
          fieldSchema = z.string().nullable().optional()
        }
        break
      case "MULTI_SELECT":
      case "MULTISELECT":
        fieldSchema = z.array(z.string()).default([])
        break
      case "ARRAY_TEXT":
      case "ARRAY_STRING":
        fieldSchema = z.array(z.string()).default([])
        break
      case "ARRAY_INTEGER":
      case "ARRAY_INT":
        fieldSchema = z.array(z.number().int()).default([])
        break
      case "ARRAY_NUMBER":
      case "ARRAY_FLOAT":
        fieldSchema = z.array(z.number()).default([])
        break
      case "JSON":
      case "OBJECT":
        fieldSchema = z.any().optional()
        break
      default:
        fieldSchema = z.any().optional()
    }

    schema[field.key] = fieldSchema
  })

  return z.object(schema)
}

export function CreateEntityRecordDialog({
  open,
  onOpenChange,
  caseId,
  workspaceId,
  onSuccess,
}: CreateEntityRecordDialogProps) {
  const [selectedEntityId, setSelectedEntityId] = useState<string>("")

  const { entities, isLoading: entitiesLoading } = useListEntities({
    workspaceId,
    includeInactive: false,
  })

  const { schema, isLoading: schemaLoading } = useGetEntitySchema({
    entityId: selectedEntityId,
    workspaceId,
  })

  const { createRecord, isCreating } = useCreateCaseRecord({
    caseId,
    workspaceId,
  })

  const formSchema = schema ? generateZodSchema(schema.fields) : z.object({})

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {},
  })

  // Reset form when entity changes
  useEffect(() => {
    if (schema) {
      const defaultValues: Record<string, unknown> = {}
      schema.fields.forEach((field) => {
        const fieldType = field.type.toUpperCase()
        if (fieldType === "BOOL" || fieldType === "BOOLEAN") {
          defaultValues[field.key] = false
        } else if (fieldType.includes("ARRAY") || fieldType.includes("MULTI")) {
          defaultValues[field.key] = []
        } else {
          defaultValues[field.key] = null
        }
      })
      form.reset(defaultValues)
    }
  }, [schema, form])

  const handleSubmit = async (values: z.infer<typeof formSchema>) => {
    if (!selectedEntityId) return

    try {
      const recordData: CaseRecordLinkCreate = {
        entity_id: selectedEntityId,
        record_data: values,
      }

      await createRecord(recordData)
      onOpenChange(false)
      form.reset()
      setSelectedEntityId("")
      onSuccess?.()
    } catch (error) {
      console.error("Failed to create entity record:", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Add entity record</DialogTitle>
          <DialogDescription>
            Select an entity type and fill in the record data to link it to this
            case.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-1">
          <div className="space-y-4">
            {/* Entity Selector */}
            <div className="space-y-2">
              <label className="text-xs font-medium">Entity Type</label>
              {entitiesLoading ? (
                <Skeleton className="h-8 w-full" />
              ) : (
                <Select
                  value={selectedEntityId}
                  onValueChange={setSelectedEntityId}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder="Select an entity type" />
                  </SelectTrigger>
                  <SelectContent>
                    {entities?.map((entity) => (
                      <SelectItem
                        key={entity.id}
                        value={entity.id}
                        className="text-xs"
                      >
                        <div className="flex items-center gap-2">
                          <span>{entity.display_name}</span>
                          <span className="text-muted-foreground">
                            ({entity.name})
                          </span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            {/* Dynamic Form Fields */}
            {selectedEntityId && (
              <>
                {schemaLoading ? (
                  <div className="space-y-3">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : schema ? (
                  <Form {...form}>
                    <form
                      onSubmit={form.handleSubmit(handleSubmit)}
                      className="space-y-4"
                    >
                      {schema.fields.map((field) => (
                        <EntityFieldInput
                          key={field.key}
                          field={field}
                          control={form.control}
                          name={field.key}
                          disabled={isCreating}
                        />
                      ))}
                    </form>
                  </Form>
                ) : null}
              </>
            )}
          </div>
        </div>

        <DialogFooter className="pt-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isCreating}
          >
            Cancel
          </Button>
          <Button
            onClick={form.handleSubmit(handleSubmit)}
            disabled={!selectedEntityId || isCreating || schemaLoading}
          >
            {isCreating && <Loader2 className="mr-2 h-3 w-3 animate-spin" />}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
