"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Settings2Icon } from "lucide-react"
import { useParams } from "next/navigation"
import { useEffect, useState } from "react"
import {
  entitiesArchiveField,
  entitiesCreateField,
  entitiesDeleteField,
  entitiesRestoreField,
  type FieldMetadataRead,
  type FieldType,
} from "@/client"
import { CreateFieldDialog } from "@/components/entities/create-field-dialog"
import { EditFieldDialog } from "@/components/entities/edit-field-dialog"
import { EntityFieldsTable } from "@/components/entities/entity-fields-table"
import { EntityRecordsTable } from "@/components/entities/entity-records-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import { entityEvents } from "@/lib/entity-events"
import { useLocalStorage } from "@/lib/hooks"
import {
  useEntity,
  useEntityFields,
  useUpdateEntityField,
} from "@/lib/hooks/use-entities"
import { useWorkspace } from "@/providers/workspace"

export default function EntityDetailPage() {
  const { workspaceId } = useWorkspace()
  const params = useParams<{ entityId: string }>()
  const entityId = params?.entityId ?? ""
  const queryClient = useQueryClient()
  const [createFieldDialogOpen, setCreateFieldDialogOpen] = useState(false)
  const [createFieldError, setCreateFieldError] = useState<string | null>(null)
  const [editFieldDialogOpen, setEditFieldDialogOpen] = useState(false)
  const [selectedFieldForEdit, setSelectedFieldForEdit] =
    useState<FieldMetadataRead | null>(null)

  const { entity, entityIsLoading, entityError } = useEntity(
    workspaceId,
    entityId
  )
  const [includeInactiveFields] = useLocalStorage(
    "entities-include-inactive",
    false
  )
  const { fields, fieldsIsLoading, fieldsError } = useEntityFields(
    workspaceId,
    entityId,
    includeInactiveFields
  )
  const { updateField, updateFieldIsPending } = useUpdateEntityField(
    workspaceId,
    entityId
  )
  const [detailView, setDetailView] = useLocalStorage(
    "entity-detail-view",
    "fields"
  )

  // Set up the callback for the Add Field button in header
  useEffect(() => {
    const handleAddField = () => setCreateFieldDialogOpen(true)
    const unsubscribe = entityEvents.onAddField(handleAddField)
    return () => {
      unsubscribe()
    }
  }, [])

  const { mutateAsync: createFieldMutation } = useMutation({
    mutationFn: async (data: {
      field_key: string
      field_type: string
      display_name: string
      description?: string
      enum_options?: string[]
      default_value?: unknown
    }) => {
      return await entitiesCreateField({
        workspaceId,
        entityId,
        requestBody: {
          field_key: data.field_key,
          field_type: data.field_type as FieldType,
          display_name: data.display_name,
          description: data.description,
          enum_options: data.enum_options,
          default_value: data.default_value,
        },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-fields", workspaceId, entityId],
      })
      queryClient.invalidateQueries({
        queryKey: ["entity-field-counts", workspaceId],
      })
      toast({
        title: "Field created",
        description: "The field was created successfully.",
      })
    },
    onError: (error: unknown) => {
      console.error("Failed to create field", error)
      // Try to extract a user-friendly message if available
      let message = "Failed to create the field. Please try again."
      if (error && typeof error === "object") {
        const err = error as { body?: { detail?: string }; message?: string }
        message = err.body?.detail || err.message || message
      }
      setCreateFieldError(message)
    },
  })

  const { mutateAsync: deactivateFieldMutation, isPending: isDeactivating } =
    useMutation({
      mutationFn: async (fieldId: string) => {
        return await entitiesArchiveField({
          workspaceId,
          fieldId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        toast({
          title: "Field archived",
          description: "The field was archived successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to archive field", error)
        toast({
          title: "Error archiving field",
          description: "Failed to archive the field. Please try again.",
          variant: "destructive",
        })
      },
    })

  const { mutateAsync: reactivateFieldMutation } = useMutation({
    mutationFn: async (fieldId: string) => {
      return await entitiesRestoreField({
        workspaceId,
        fieldId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-fields", workspaceId, entityId],
      })
      toast({
        title: "Field restored",
        description: "The field was restored successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to restore field", error)
      toast({
        title: "Error restoring field",
        description: "Failed to restore the field. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: deleteFieldMutation, isPending: isDeletingField } =
    useMutation({
      mutationFn: async (fieldId: string) => {
        return await entitiesDeleteField({
          workspaceId,
          fieldId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-field-counts", workspaceId],
        })
        toast({
          title: "Field deleted",
          description: "The field and all its data were permanently deleted.",
        })
      },
      onError: (error) => {
        console.error("Failed to delete field", error)
        toast({
          title: "Error deleting field",
          description: "Failed to delete the field. Please try again.",
          variant: "destructive",
        })
      },
    })

  if (entityIsLoading || fieldsIsLoading) {
    return <CenteredSpinner />
  }

  if (entityError || !entity) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading entity: ${entityError?.message || "Unknown error"}`}
      />
    )
  }

  if (fieldsError || !fields) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading fields: ${fieldsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container max-w-[1200px] my-16">
        {/* Local view toggle: Fields | Records */}
        <div className="mb-4 inline-flex items-center rounded-md border">
          <Button
            type="button"
            variant={detailView === "fields" ? "secondary" : "ghost"}
            className="h-7 rounded-r-none"
            onClick={() => setDetailView("fields")}
          >
            Fields
          </Button>
          <Button
            type="button"
            variant={detailView === "records" ? "secondary" : "ghost"}
            className="h-7 rounded-l-none"
            onClick={() => setDetailView("records")}
          >
            Records
          </Button>
        </div>
        <div className="space-y-4">
          {detailView === "records" ? (
            <EntityRecordsTable entityId={entity.id} />
          ) : fields.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-12">
                <Settings2Icon className="h-12 w-12 text-muted-foreground mb-4" />
                <h3 className="text-sm font-semibold mb-1">No fields yet</h3>
                <p className="text-xs text-muted-foreground text-center max-w-[300px]">
                  Add fields to define the structure of your records
                </p>
              </CardContent>
            </Card>
          ) : (
            <EntityFieldsTable
              fields={fields}
              onEditField={(field) => {
                setSelectedFieldForEdit(field)
                setEditFieldDialogOpen(true)
              }}
              onDeleteField={async (fieldId) => {
                await deleteFieldMutation(fieldId)
              }}
              onDeactivateField={async (fieldId) => {
                await deactivateFieldMutation(fieldId)
              }}
              onReactivateField={async (fieldId) => {
                await reactivateFieldMutation(fieldId)
              }}
              isDeleting={isDeactivating || isDeletingField}
            />
          )}
        </div>
      </div>

      <CreateFieldDialog
        open={createFieldDialogOpen}
        onOpenChange={(open) => {
          setCreateFieldDialogOpen(open)
          if (!open) {
            setCreateFieldError(null) // reset error when closing dialog
          }
        }}
        errorMessage={createFieldError || undefined}
        onSubmit={async (data) => {
          try {
            setCreateFieldError(null) // clear previous errors
            await createFieldMutation(data)
          } catch (error) {
            console.error("Failed to create field:", error)
            // Ensure the dialog also captures this error
            // and display a user-friendly message in the parent
            let message = "Failed to create the field. Please try again."
            if (error && typeof error === "object") {
              const err = error as {
                body?: {
                  detail?: string | string[]
                  message?: string
                  error?: string
                }
                message?: string
                status?: number
                statusText?: string
              }
              const detail = err.body?.detail
              if (Array.isArray(detail)) {
                message = detail.join("\n")
              } else {
                message =
                  (typeof detail === "string" && detail) ||
                  err.body?.message ||
                  err.body?.error ||
                  (err.status && err.statusText
                    ? `${err.status} ${err.statusText}`
                    : err.message) ||
                  message
              }
            }
            setCreateFieldError(message)
            throw error
          }
        }}
      />

      <EditFieldDialog
        field={selectedFieldForEdit}
        open={editFieldDialogOpen}
        onOpenChange={(open) => {
          setEditFieldDialogOpen(open)
          if (!open) {
            setSelectedFieldForEdit(null)
          }
        }}
        onSubmit={async (fieldId, data) => {
          try {
            await updateField({ fieldId, data })
          } catch (error) {
            // Error is already handled by mutation's onError callback
            console.error("Failed to update field:", error)
          }
        }}
        isPending={updateFieldIsPending}
      />
    </div>
  )
}
