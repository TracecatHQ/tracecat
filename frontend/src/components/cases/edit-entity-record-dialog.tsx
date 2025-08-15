"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type {
  CaseRecordLinkRead,
  EntitySchemaField,
  RecordUpdate,
} from "@/client"
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
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useGetEntitySchema,
  useListEntityFields,
  useUpdateCaseRecord,
} from "@/lib/hooks"

interface EditEntityRecordDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  recordLink: CaseRecordLinkRead
  workspaceId: string
  onSuccess?: () => void
}

function generateZodSchema(
  fields: EntitySchemaField[],
  relationFields?: Map<string, EntitySchemaField[]>
): z.ZodObject<Record<string, z.ZodTypeAny>> {
  const schema: Record<string, z.ZodTypeAny> = {}

  // Process main entity fields
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
      case "RELATION_BELONGS_TO":
        // For belongs_to relations, we create a nested object for the related entity
        if (relationFields) {
          const relationKey = field.key
          const relatedFields = relationFields.get(relationKey)
          if (relatedFields) {
            // Create a nested schema for the related entity
            const nestedSchema: Record<string, z.ZodTypeAny> = {}
            relatedFields.forEach((relField) => {
              nestedSchema[relField.key] = generateFieldSchema(relField)
            })
            fieldSchema = z.object(nestedSchema).nullable().optional()
          } else {
            fieldSchema = z.any().nullable().optional()
          }
        } else {
          fieldSchema = z.any().nullable().optional()
        }
        break
      default:
        fieldSchema = z.any().optional()
    }

    schema[field.key] = fieldSchema
  })

  return z.object(schema)
}

// Helper function to generate schema for individual fields
function generateFieldSchema(field: EntitySchemaField): z.ZodTypeAny {
  const fieldType = field.type.toUpperCase()

  switch (fieldType) {
    case "TEXT":
    case "STRING":
    case "LONGTEXT":
    case "TEXTAREA":
      return z.string().nullable().optional()
    case "INTEGER":
    case "INT":
      return z.number().int().nullable().optional()
    case "NUMBER":
    case "FLOAT":
    case "DECIMAL":
      return z.number().nullable().optional()
    case "BOOL":
    case "BOOLEAN":
      return z.boolean().default(false)
    case "DATE":
      return z.string().nullable().optional()
    case "DATETIME":
    case "TIMESTAMP":
      return z.string().nullable().optional()
    case "SELECT":
    case "ENUM":
      if (field.enum_options && field.enum_options.length > 0) {
        return z
          .enum(field.enum_options as [string, ...string[]])
          .nullable()
          .optional()
      } else {
        return z.string().nullable().optional()
      }
    case "MULTI_SELECT":
    case "MULTISELECT":
      return z.array(z.string()).default([])
    case "ARRAY_TEXT":
    case "ARRAY_STRING":
      return z.array(z.string()).default([])
    case "ARRAY_INTEGER":
    case "ARRAY_INT":
      return z.array(z.number().int()).default([])
    case "ARRAY_NUMBER":
    case "ARRAY_FLOAT":
      return z.array(z.number()).default([])
    case "JSON":
    case "OBJECT":
      return z.any().optional()
    default:
      return z.any().optional()
  }
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
  const [relationSchemas, setRelationSchemas] = useState<
    Map<string, EntitySchemaField[]>
  >(new Map())
  const [relationEntityNames, setRelationEntityNames] = useState<
    Map<string, string>
  >(new Map())

  const { schema, isLoading: schemaLoading } = useGetEntitySchema({
    entityId,
    workspaceId,
  })

  const { fields: fullFields, isLoading: fieldsLoading } = useListEntityFields({
    entityId,
    workspaceId,
  })

  const { updateRecord, isUpdating } = useUpdateCaseRecord({
    caseId,
    recordId: recordLink.record?.id || recordLink.record_id,
    workspaceId,
  })

  // Separate regular fields and relation fields
  const { regularFields, relationFields } = useMemo(() => {
    if (!schema?.fields) return { regularFields: [], relationFields: [] }

    const regular: EntitySchemaField[] = []
    const relations: Array<EntitySchemaField & { targetEntityId?: string }> = []

    schema.fields.forEach((field) => {
      const fieldType = field.type.toUpperCase()
      if (fieldType === "RELATION_BELONGS_TO") {
        // Find the full field metadata to get target entity ID
        const fullField = fullFields?.find((f) => f.field_key === field.key)
        relations.push({
          ...field,
          targetEntityId: fullField?.target_entity_id || undefined,
        })
      } else if (fieldType !== "RELATION_HAS_MANY") {
        // Skip HAS_MANY relations as they're not edited inline
        regular.push(field)
      }
    })

    return { regularFields: regular, relationFields: relations }
  }, [schema, fullFields])

  // Fetch schemas for relation fields
  useEffect(() => {
    const fetchRelationSchemas = async () => {
      if (relationFields.length === 0) {
        setRelationSchemas(new Map())
        setRelationEntityNames(new Map())
        return
      }

      const newSchemas = new Map<string, EntitySchemaField[]>()
      const newEntityNames = new Map<string, string>()

      for (const field of relationFields) {
        if (field.targetEntityId) {
          try {
            const { entitiesGetEntitySchema } = await import("@/client")
            const targetSchema = await entitiesGetEntitySchema({
              entityId: field.targetEntityId,
              workspaceId,
            })

            // Filter out relation fields to prevent recursive nesting
            const filteredFields = targetSchema.fields.filter((field) => {
              const fieldType = field.type.toUpperCase()
              return (
                fieldType !== "RELATION_BELONGS_TO" &&
                fieldType !== "RELATION_HAS_MANY"
              )
            })

            newSchemas.set(field.key, filteredFields)
            newEntityNames.set(field.key, targetSchema.entity.display_name)
          } catch (error) {
            console.error(
              `Failed to fetch schema for relation field ${field.key}:`,
              error
            )
          }
        }
      }

      setRelationSchemas(newSchemas)
      setRelationEntityNames(newEntityNames)
    }

    fetchRelationSchemas()
  }, [relationFields, workspaceId])

  const formSchema = useMemo(() => {
    if (!schema) return z.object({})
    return generateZodSchema(schema.fields, relationSchemas)
  }, [schema, relationSchemas])

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {},
  })

  // Initialize form with existing data
  useEffect(() => {
    if (recordLink.record?.field_data && schema?.fields) {
      const formattedData: Record<string, unknown> = {}

      // Parse the field data and format it for the form
      Object.entries(recordLink.record.field_data).forEach(([key, value]) => {
        const field = schema.fields.find((f) => f.key === key)
        if (field) {
          const fieldType = field.type.toUpperCase()
          // Handle special formatting for dates and arrays
          if (
            fieldType === "DATE" ||
            fieldType === "DATETIME" ||
            fieldType === "TIMESTAMP"
          ) {
            formattedData[key] = value as string
          } else if (
            fieldType === "MULTI_SELECT" ||
            fieldType === "MULTISELECT" ||
            fieldType === "ARRAY_TEXT" ||
            fieldType === "ARRAY_STRING" ||
            fieldType === "ARRAY_INTEGER" ||
            fieldType === "ARRAY_INT" ||
            fieldType === "ARRAY_NUMBER" ||
            fieldType === "ARRAY_FLOAT" ||
            fieldType === "RELATION_HAS_MANY"
          ) {
            formattedData[key] = Array.isArray(value) ? value : []
          } else if (fieldType === "RELATION_BELONGS_TO") {
            // Handle relation data - it could be an object or null
            formattedData[key] = value || {}
          } else {
            formattedData[key] = value
          }
        }
      })

      form.reset(formattedData)
    }
  }, [recordLink.record?.field_data, schema, form])

  const handleSubmit = async (values: z.infer<typeof formSchema>) => {
    try {
      const updateData: RecordUpdate = values
      await updateRecord(updateData)
      onOpenChange(false)
      onSuccess?.()
    } catch (error) {
      console.error("Failed to update entity record:", error)
    }
  }

  const isLoadingData =
    schemaLoading ||
    fieldsLoading ||
    (relationFields.length > 0 && relationSchemas.size < relationFields.length)

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>Edit entity record</DialogTitle>
            <DialogDescription>
              Update the fields for this{" "}
              {schema?.entity?.display_name || "entity"} record
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto px-1 py-0.5">
            {isLoadingData ? (
              <div className="space-y-4 py-4">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : schema?.fields && schema.fields.length > 0 ? (
              <Form {...form}>
                <form
                  onSubmit={form.handleSubmit(handleSubmit)}
                  className="space-y-6"
                >
                  {/* Main Entity Fields */}
                  {regularFields.length > 0 && (
                    <div className="space-y-4">
                      {regularFields.length > 0 &&
                        relationFields.length > 0 && (
                          <h3 className="text-sm font-medium text-muted-foreground">
                            {schema.entity.display_name} fields
                          </h3>
                        )}
                      {regularFields.map((field) => (
                        <EntityFieldInput
                          key={field.key}
                          field={field}
                          control={form.control}
                          name={field.key}
                          disabled={isUpdating}
                        />
                      ))}
                    </div>
                  )}

                  {/* Relation Fields */}
                  {relationFields.map((field) => {
                    const relatedSchema = relationSchemas.get(field.key)
                    const entityName = relationEntityNames.get(field.key)

                    if (!relatedSchema || relatedSchema.length === 0)
                      return null

                    return (
                      <div key={field.key} className="space-y-4">
                        <Separator />
                        <div className="space-y-4">
                          <div>
                            <h3 className="text-sm font-medium">
                              {field.display_name}
                            </h3>
                            {entityName && (
                              <p className="text-xs text-muted-foreground mt-1">
                                Edit {entityName}
                              </p>
                            )}
                          </div>
                          {relatedSchema.map((relField) => (
                            <EntityFieldInput
                              key={`${field.key}.${relField.key}`}
                              field={relField}
                              control={form.control}
                              name={`${field.key}.${relField.key}`}
                              disabled={isUpdating}
                            />
                          ))}
                        </div>
                      </div>
                    )
                  })}
                </form>
              </Form>
            ) : (
              <div className="py-8 text-center text-muted-foreground">
                No fields available for this entity
              </div>
            )}
          </div>

          <DialogFooter className="pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isUpdating}
            >
              Cancel
            </Button>
            <Button
              onClick={form.handleSubmit(handleSubmit)}
              disabled={isUpdating || isLoadingData}
            >
              {isUpdating ? (
                <>
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                  Saving...
                </>
              ) : (
                "Save changes"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
