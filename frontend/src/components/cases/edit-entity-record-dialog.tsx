"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Database, Link, Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type {
  CaseRecordLinkRead,
  EntitySchemaField,
  RecordUpdate,
} from "@/client"
import { EntityFieldInput } from "@/components/cases/entity-field-input"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
} from "@/components/ui/dialog"
import { Form } from "@/components/ui/form"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useGetEntitySchema,
  useListEntities,
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
      case "RELATION_ONE_TO_ONE":
      case "RELATION_MANY_TO_ONE":
        // For one_to_one relations, we create a nested object for the related entity
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
  const [currentStep, setCurrentStep] = useState<1 | 2>(1)
  const [relationSchemas, setRelationSchemas] = useState<
    Map<string, EntitySchemaField[]>
  >(new Map())
  const [relationEntityNames, setRelationEntityNames] = useState<
    Map<string, string>
  >(new Map())

  const { entities } = useListEntities({
    workspaceId,
    includeInactive: false,
  })

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
      if (
        fieldType === "RELATION_ONE_TO_ONE" ||
        fieldType === "RELATION_MANY_TO_ONE"
      ) {
        // Find the full field metadata to get target entity ID
        const fullField = fullFields?.find((f) => f.field_key === field.key)
        relations.push({
          ...field,
          targetEntityId: fullField?.target_entity_id || undefined,
        })
      } else if (
        fieldType !== "RELATION_ONE_TO_MANY" &&
        fieldType !== "RELATION_MANY_TO_MANY"
      ) {
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
                fieldType !== "RELATION_ONE_TO_ONE" &&
                fieldType !== "RELATION_ONE_TO_MANY" &&
                fieldType !== "RELATION_MANY_TO_ONE" &&
                fieldType !== "RELATION_MANY_TO_MANY"
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
    // Include all fields with relation schemas for nested updates
    return generateZodSchema(schema.fields, relationSchemas)
  }, [schema, relationSchemas])

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {},
  })

  // Initialize form with existing data (including relation fields for nested updates)
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
            fieldType === "ARRAY_FLOAT"
          ) {
            formattedData[key] = Array.isArray(value) ? value : []
          } else if (
            fieldType === "RELATION_ONE_TO_MANY" ||
            fieldType === "RELATION_MANY_TO_MANY"
          ) {
            // HAS_MANY relations are arrays of related records
            formattedData[key] = Array.isArray(value) ? value : []
          } else if (
            fieldType === "RELATION_ONE_TO_ONE" ||
            fieldType === "RELATION_MANY_TO_ONE"
          ) {
            // BELONGS_TO relations - initialize as empty object if null
            // This allows users to fill in the fields
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
      // Filter out empty relation objects to prevent invalid updates
      const filteredValues = Object.entries(values).reduce(
        (acc, [key, value]) => {
          const field = schema?.fields.find((f) => f.key === key)
          const fieldType = field?.type.toUpperCase()

          // Skip empty relation objects (null relations that weren't edited)
          if (
            (fieldType === "RELATION_ONE_TO_ONE" ||
              fieldType === "RELATION_ONE_TO_MANY" ||
              fieldType === "RELATION_MANY_TO_ONE" ||
              fieldType === "RELATION_MANY_TO_MANY") &&
            (value === null ||
              value === undefined ||
              (typeof value === "object" && Object.keys(value).length === 0))
          ) {
            // Don't include empty relations in update
            return acc
          }

          acc[key] = value
          return acc
        },
        {} as Record<string, unknown>
      )

      // Send filtered values including valid nested relation updates
      const updateData: RecordUpdate = filteredValues
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

  // Check if there are any fillable fields (regular fields or relations with fields)
  const hasFillableFields = useMemo(() => {
    if (regularFields.length > 0) return true

    // Check if any relation has fillable fields
    for (const field of relationFields) {
      const relatedSchema = relationSchemas.get(field.key)
      if (relatedSchema && relatedSchema.length > 0) {
        return true
      }
    }

    return false
  }, [regularFields, relationFields, relationSchemas])

  // Helper function to handle step navigation
  const handleStepChange = (step: 1 | 2) => {
    setCurrentStep(step)
  }

  // Check if we have relations to show
  const hasRelations =
    relationFields.length > 0 &&
    relationFields.some((field) => {
      const relatedSchema = relationSchemas.get(field.key)
      return relatedSchema && relatedSchema.length > 0
    })

  // Set initial step when dialog opens
  useEffect(() => {
    if (open && schema) {
      if (regularFields.length > 0) {
        setCurrentStep(1)
      } else if (hasRelations) {
        setCurrentStep(2)
      }
    }
  }, [open, schema, regularFields.length, hasRelations])

  // Get selected entity name
  const selectedEntity = entities?.find((e) => e.id === entityId)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <div className="space-y-4">
            {regularFields.length > 0 && hasRelations && (
              <Breadcrumb>
                <BreadcrumbList>
                  <BreadcrumbItem>
                    {currentStep === 1 ? (
                      <BreadcrumbPage className="flex items-center gap-2">
                        <Database className="h-4 w-4" />
                        Fields
                      </BreadcrumbPage>
                    ) : (
                      <BreadcrumbLink
                        className="flex items-center gap-2 cursor-pointer"
                        onClick={() => handleStepChange(1)}
                      >
                        <Database className="h-4 w-4" />
                        Fields
                      </BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                  <BreadcrumbSeparator />
                  <BreadcrumbItem>
                    {currentStep === 2 ? (
                      <BreadcrumbPage className="flex items-center gap-2">
                        <Link className="h-4 w-4" />
                        Related entities
                      </BreadcrumbPage>
                    ) : (
                      <BreadcrumbLink
                        className="flex items-center gap-2 cursor-pointer"
                        onClick={() => handleStepChange(2)}
                      >
                        <Link className="h-4 w-4" />
                        Related entities
                      </BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                </BreadcrumbList>
              </Breadcrumb>
            )}
            <DialogDescription>
              {currentStep === 1 &&
                regularFields.length > 0 &&
                `Update field data for ${selectedEntity?.display_name || "entity"}.`}
              {currentStep === 2 &&
                `Update related entity data for ${selectedEntity?.display_name || "entity"}.`}
              {!regularFields.length &&
                hasRelations &&
                `Update related entity data for ${selectedEntity?.display_name || "entity"}.`}
            </DialogDescription>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-1 py-0.5">
          <div className="space-y-6">
            {/* Step 1: Regular Fields */}
            {currentStep === 1 && regularFields.length > 0 && (
              <>
                {isLoadingData ? (
                  <div className="space-y-3">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : schema ? (
                  <Form {...form}>
                    <form
                      onSubmit={form.handleSubmit(handleSubmit)}
                      className="space-y-6"
                    >
                      {/* Main Entity Fields - No Badge */}
                      <div className="space-y-4">
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
                    </form>
                  </Form>
                ) : null}
              </>
            )}

            {/* Step 2: Relation Fields Carousel */}
            {currentStep === 2 && hasRelations && (
              <>
                {isLoadingData ? (
                  <div className="space-y-3">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : schema ? (
                  <Form {...form}>
                    <form
                      onSubmit={form.handleSubmit(handleSubmit)}
                      className="space-y-6"
                    >
                      <div className="px-12">
                        <Carousel className="w-full">
                          <CarouselContent>
                            {(() => {
                              let visibleIndex = 0
                              const visibleRelationsCount =
                                relationFields.filter((f) => {
                                  const schema = relationSchemas.get(f.key)
                                  return schema && schema.length > 0
                                }).length

                              return relationFields.map((field) => {
                                const relatedSchema = relationSchemas.get(
                                  field.key
                                )
                                const entityName = relationEntityNames.get(
                                  field.key
                                )

                                if (
                                  !relatedSchema ||
                                  relatedSchema.length === 0
                                )
                                  return null

                                visibleIndex++
                                const currentIndex = visibleIndex

                                return (
                                  <CarouselItem key={field.key}>
                                    <div className="p-1">
                                      <div className="space-y-6 pt-3">
                                        <div>
                                          <h3 className="text-base font-medium">
                                            {field.display_name}
                                            {visibleRelationsCount > 1 && (
                                              <span className="text-muted-foreground ml-2 text-sm">
                                                ({currentIndex} of{" "}
                                                {visibleRelationsCount})
                                              </span>
                                            )}
                                          </h3>
                                          {entityName && (
                                            <p className="text-xs text-muted-foreground mt-1">
                                              Update {entityName}
                                            </p>
                                          )}
                                        </div>
                                        <div className="space-y-4">
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
                                    </div>
                                  </CarouselItem>
                                )
                              })
                            })()}
                          </CarouselContent>
                          {relationFields.filter((field) => {
                            const relatedSchema = relationSchemas.get(field.key)
                            return relatedSchema && relatedSchema.length > 0
                          }).length > 1 && (
                            <>
                              <CarouselPrevious />
                              <CarouselNext />
                            </>
                          )}
                        </Carousel>
                      </div>
                    </form>
                  </Form>
                ) : null}
              </>
            )}

            {/* No fields message */}
            {!isLoadingData && schema && !hasFillableFields && (
              <div className="flex flex-col items-center justify-center py-8">
                <div className="p-2 rounded-full bg-muted/50 mb-3">
                  <Database className="h-5 w-5 text-muted-foreground" />
                </div>
                <h3 className="text-sm font-medium text-muted-foreground mb-1">
                  No fields found
                </h3>
                <p className="text-xs text-muted-foreground/75 text-center max-w-[250px]">
                  The selected entity requires at least one field to be filled
                  in.
                </p>
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="pt-4">
          <div className="flex items-center justify-end w-full">
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  onOpenChange(false)
                  setCurrentStep(1)
                  form.reset()
                }}
                disabled={isUpdating}
              >
                Cancel
              </Button>

              {/* Next Button for Step 1 */}
              {currentStep === 1 &&
                regularFields.length > 0 &&
                hasRelations && (
                  <Button
                    onClick={() => setCurrentStep(2)}
                    disabled={isUpdating}
                  >
                    Next
                  </Button>
                )}

              {/* Save Button for Final Step or Step 1 if no relations */}
              {((currentStep === 1 && !hasRelations) || currentStep === 2) && (
                <Button
                  onClick={form.handleSubmit(handleSubmit)}
                  disabled={
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
              )}
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
