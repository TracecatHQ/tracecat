"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Settings2Icon } from "lucide-react"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useEffect, useState } from "react"
import {
  entitiesCreateField,
  entitiesCreateRelationField,
  entitiesDeactivateEntityType,
  entitiesDeactivateField,
  entitiesReactivateEntityType,
  entitiesReactivateField,
  entitiesUpdateEntityType,
  type FieldMetadataRead,
  type FieldType,
  type RelationSettings,
} from "@/client"
import { CreateFieldDialog } from "@/components/entities/create-field-dialog"
import { EditFieldDialog } from "@/components/entities/edit-field-dialog"
import { EntityFieldsTable } from "@/components/entities/entity-fields-table"
import { IconPicker } from "@/components/form/icon-picker"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { entityEvents } from "@/lib/entity-events"
import {
  useEntities,
  useEntity,
  useEntityFields,
  useUpdateEntityField,
} from "@/lib/hooks/use-entities"
import { getIconByName } from "@/lib/icon-data"
import { useWorkspace } from "@/providers/workspace"

export default function EntityDetailPage() {
  const { workspaceId } = useWorkspace()
  const router = useRouter()
  const params = useParams<{ entityId: string }>()
  const entityId = params.entityId
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const [createFieldDialogOpen, setCreateFieldDialogOpen] = useState(false)
  const [createFieldError, setCreateFieldError] = useState<string | null>(null)
  const [editFieldDialogOpen, setEditFieldDialogOpen] = useState(false)
  const [selectedFieldForEdit, setSelectedFieldForEdit] =
    useState<FieldMetadataRead | null>(null)
  const [isEditingSettings, setIsEditingSettings] = useState(false)
  const [deactivateDialogOpen, setDeactivateDialogOpen] = useState(false)
  const [selectedIcon, setSelectedIcon] = useState<string | undefined>()
  const [displayName, setDisplayName] = useState<string>("")
  const [description, setDescription] = useState<string>("")

  const currentTab = searchParams?.get("tab") || "fields"

  const { entity, entityIsLoading, entityError } = useEntity(
    workspaceId,
    entityId
  )
  const { fields, fieldsIsLoading, fieldsError } = useEntityFields(
    workspaceId,
    entityId
  )
  const { updateField, updateFieldIsPending } = useUpdateEntityField(
    workspaceId,
    entityId
  )
  const { entities } = useEntities(workspaceId)

  // Initialize form state when entity loads or when starting to edit
  useEffect(() => {
    if (entity) {
      setDisplayName(entity.display_name)
      setDescription(entity.description || "")
      if (isEditingSettings) {
        setSelectedIcon(entity.icon || undefined)
      }
    }
  }, [entity, isEditingSettings])

  // Set up the callback for the Add Field button in header
  useEffect(() => {
    const handleAddField = () => setCreateFieldDialogOpen(true)
    const unsubscribe = entityEvents.onAddField(handleAddField)
    return unsubscribe
  }, [])

  const { mutateAsync: createFieldMutation } = useMutation({
    mutationFn: async (data: {
      field_key: string
      field_type: string
      display_name: string
      description?: string
      enum_options?: string[]
      relation_settings?: RelationSettings
      default_value?: unknown
    }) => {
      // Use different endpoint for relation fields
      if (
        data.field_type === "RELATION_BELONGS_TO" ||
        data.field_type === "RELATION_HAS_MANY"
      ) {
        return await entitiesCreateRelationField({
          workspaceId,
          entityId,
          requestBody: {
            field_key: data.field_key,
            field_type: data.field_type as FieldType,
            display_name: data.display_name,
            description: data.description,
            relation_settings: data.relation_settings,
          },
        })
      } else {
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
      }
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
        return await entitiesDeactivateField({
          workspaceId,
          fieldId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entity-fields", workspaceId, entityId],
        })
        toast({
          title: "Field deactivated",
          description: "The field was deactivated successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to deactivate field", error)
        toast({
          title: "Error deactivating field",
          description: "Failed to deactivate the field. Please try again.",
          variant: "destructive",
        })
      },
    })

  const { mutateAsync: reactivateFieldMutation } = useMutation({
    mutationFn: async (fieldId: string) => {
      return await entitiesReactivateField({
        workspaceId,
        fieldId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity-fields", workspaceId, entityId],
      })
      toast({
        title: "Field reactivated",
        description: "The field was reactivated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to reactivate field", error)
      toast({
        title: "Error reactivating field",
        description: "Failed to reactivate the field. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: updateEntityMutation } = useMutation({
    mutationFn: async (data: {
      display_name?: string
      description?: string
      icon?: string
    }) => {
      return await entitiesUpdateEntityType({
        workspaceId,
        entityId,
        requestBody: data,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entity", workspaceId, entityId],
      })
      queryClient.invalidateQueries({
        queryKey: ["entities", workspaceId],
      })
      setIsEditingSettings(false)
      toast({
        title: "Entity updated",
        description: "The entity settings were updated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to update entity", error)
      toast({
        title: "Error updating entity",
        description: "Failed to update the entity. Please try again.",
        variant: "destructive",
      })
    },
  })

  const {
    mutateAsync: deactivateEntityMutation,
    isPending: isDeactivatingEntity,
  } = useMutation({
    mutationFn: async () => {
      return await entitiesDeactivateEntityType({
        workspaceId,
        entityId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entities", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["entity", workspaceId, entityId],
      })
      toast({
        title: "Entity deactivated",
        description: "The entity was deactivated successfully.",
      })
      // Navigate back to entities list
      router.push(`/workspaces/${workspaceId}/entities`)
    },
    onError: (error) => {
      console.error("Failed to deactivate entity", error)
      toast({
        title: "Error deactivating entity",
        description: "Failed to deactivate the entity. Please try again.",
        variant: "destructive",
      })
    },
  })

  const {
    mutateAsync: reactivateEntityMutation,
    isPending: isReactivatingEntity,
  } = useMutation({
    mutationFn: async () => {
      return await entitiesReactivateEntityType({
        workspaceId,
        entityId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["entities", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["entity", workspaceId, entityId],
      })
      toast({
        title: "Entity reactivated",
        description: "The entity was reactivated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to reactivate entity", error)
      toast({
        title: "Error reactivating entity",
        description: "Failed to reactivate the entity. Please try again.",
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
        {currentTab === "fields" ? (
          <div className="space-y-4">
            {fields.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-12">
                  <Settings2Icon className="h-12 w-12 text-muted-foreground mb-4" />
                  <h3 className="text-sm font-semibold mb-1">No fields yet</h3>
                  <p className="text-xs text-muted-foreground text-center max-w-[300px]">
                    Add fields to define the structure of your{" "}
                    {entity.display_name.toLowerCase()} records
                  </p>
                </CardContent>
              </Card>
            ) : (
              <EntityFieldsTable
                fields={fields}
                entities={entities}
                currentEntityName={entity?.display_name}
                onEditField={(field) => {
                  setSelectedFieldForEdit(field)
                  setEditFieldDialogOpen(true)
                }}
                onDeactivateField={async (fieldId) => {
                  await deactivateFieldMutation(fieldId)
                }}
                onReactivateField={async (fieldId) => {
                  await reactivateFieldMutation(fieldId)
                }}
                isDeleting={isDeactivating}
              />
            )}
          </div>
        ) : (
          <div className="space-y-8">
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-medium">About</h3>
              </div>
              <Card>
                <CardContent className="pt-6">
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="name">Identifier / Slug</Label>
                      <Input
                        id="name"
                        value={entity.name}
                        disabled
                        className="bg-muted max-w-md"
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="display_name">Name</Label>
                      <Input
                        id="display_name"
                        value={displayName}
                        onChange={(e) => setDisplayName(e.target.value)}
                        disabled={!isEditingSettings}
                        className="max-w-md"
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="description">Description</Label>
                      <Textarea
                        id="description"
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        disabled={!isEditingSettings}
                        className="text-xs resize-none max-w-md"
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="icon">Icon</Label>
                      <div className="max-w-md">
                        {isEditingSettings ? (
                          <IconPicker
                            value={selectedIcon}
                            onValueChange={setSelectedIcon}
                            placeholder="Select an icon"
                            className="w-full"
                          />
                        ) : (
                          <div className="flex items-center gap-2">
                            {entity.icon &&
                              (() => {
                                const IconComponent = getIconByName(entity.icon)
                                const initials =
                                  entity.display_name?.[0]?.toUpperCase() || "?"
                                return (
                                  <Avatar className="size-8">
                                    <AvatarFallback className="text-sm">
                                      {IconComponent ? (
                                        <IconComponent className="size-4" />
                                      ) : (
                                        initials
                                      )}
                                    </AvatarFallback>
                                  </Avatar>
                                )
                              })()}
                            <span className="text-sm text-muted-foreground">
                              {entity.icon || "No icon selected"}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex gap-2">
                      {isEditingSettings ? (
                        <>
                          <Button
                            onClick={async () => {
                              await updateEntityMutation({
                                display_name: displayName,
                                description: description,
                                icon: selectedIcon ?? undefined,
                              })
                            }}
                          >
                            Save changes
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => {
                              setIsEditingSettings(false)
                              // Reset form to original values
                              setDisplayName(entity.display_name)
                              setDescription(entity.description || "")
                              setSelectedIcon(entity.icon || undefined)
                            }}
                          >
                            Cancel
                          </Button>
                        </>
                      ) : (
                        <Button onClick={() => setIsEditingSettings(true)}>
                          Edit settings
                        </Button>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>

            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-medium">Danger zone</h3>
              </div>
              <Card>
                <CardContent className="pt-6">
                  <div className="space-y-4">
                    {entity.is_active ? (
                      <div className="flex items-center justify-between">
                        <div className="space-y-1">
                          <p className="text-sm font-medium">
                            Deactivate this entity
                          </p>
                          <p className="text-sm text-muted-foreground">
                            Deactivating will hide this entity from normal use,
                            but all data will be preserved. You can reactivate
                            it later.
                          </p>
                        </div>
                        <AlertDialog
                          open={deactivateDialogOpen}
                          onOpenChange={setDeactivateDialogOpen}
                        >
                          <AlertDialogTrigger asChild>
                            <Button
                              variant="outline"
                              disabled={isDeactivatingEntity}
                            >
                              Deactivate entity
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>
                                Deactivate entity
                              </AlertDialogTitle>
                              <AlertDialogDescription>
                                Are you sure you want to deactivate the entity{" "}
                                <strong>{entity.display_name}</strong>? This
                                will hide the entity from normal use, but all
                                data will be preserved. You can reactivate it
                                later.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel
                                disabled={isDeactivatingEntity}
                              >
                                Cancel
                              </AlertDialogCancel>
                              <AlertDialogAction
                                onClick={async () => {
                                  try {
                                    await deactivateEntityMutation()
                                  } catch (error) {
                                    console.error(
                                      "Failed to deactivate entity:",
                                      error
                                    )
                                  }
                                }}
                                disabled={isDeactivatingEntity}
                              >
                                {isDeactivatingEntity
                                  ? "Deactivating..."
                                  : "Deactivate"}
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    ) : (
                      <div className="flex items-center justify-between">
                        <div className="space-y-1">
                          <p className="text-sm font-medium">
                            Reactivate this entity
                          </p>
                          <p className="text-sm text-muted-foreground">
                            This entity is currently inactive. Reactivate it to
                            use it again.
                          </p>
                        </div>
                        <Button
                          onClick={async () => {
                            try {
                              await reactivateEntityMutation()
                            } catch (error) {
                              console.error(
                                "Failed to reactivate entity:",
                                error
                              )
                            }
                          }}
                          disabled={isReactivatingEntity}
                        >
                          {isReactivatingEntity
                            ? "Reactivating..."
                            : "Reactivate entity"}
                        </Button>
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        )}
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
