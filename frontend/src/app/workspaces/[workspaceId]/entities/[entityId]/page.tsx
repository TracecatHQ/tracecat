"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Settings2Icon } from "lucide-react"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useEffect, useState } from "react"
import {
  entitiesCreateField,
  entitiesDeactivateEntityType,
  entitiesDeactivateField,
  entitiesReactivateEntityType,
  entitiesReactivateField,
  entitiesUpdateEntityType,
  type FieldType,
} from "@/client"
import { CreateFieldDialog } from "@/components/entities/create-field-dialog"
import { EntityFieldsTable } from "@/components/entities/entity-fields-table"
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
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { entityEvents } from "@/lib/entity-events"
import { useEntity, useEntityFields } from "@/lib/hooks/use-entities"
import { useWorkspace } from "@/providers/workspace"

export default function EntityDetailPage() {
  const { workspaceId } = useWorkspace()
  const router = useRouter()
  const params = useParams<{ entityId: string }>()
  const entityId = params.entityId
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const [createFieldDialogOpen, setCreateFieldDialogOpen] = useState(false)
  const [isEditingSettings, setIsEditingSettings] = useState(false)
  const [deactivateDialogOpen, setDeactivateDialogOpen] = useState(false)

  const currentTab = searchParams?.get("tab") || "fields"

  const { entity, entityIsLoading, entityError } = useEntity(
    workspaceId,
    entityId
  )
  const { fields, fieldsIsLoading, fieldsError } = useEntityFields(
    workspaceId,
    entityId
  )

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
      is_required: boolean
      is_unique: boolean
    }) => {
      return await entitiesCreateField({
        workspaceId,
        entityId,
        requestBody: {
          field_key: data.field_key,
          field_type: data.field_type as FieldType,
          display_name: data.display_name,
          description: data.description,
          field_settings: {},
          is_required: data.is_required,
          is_unique: data.is_unique,
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
    onError: (error) => {
      console.error("Failed to create field", error)
      toast({
        title: "Error creating field",
        description: "Failed to create the field. Please try again.",
        variant: "destructive",
      })
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
                      <p className="text-xs text-muted-foreground">
                        This cannot be changed after creation
                      </p>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="display_name">Name</Label>
                      <Input
                        id="display_name"
                        defaultValue={entity.display_name}
                        disabled={!isEditingSettings}
                        className="max-w-md"
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="description">Description</Label>
                      <Textarea
                        id="description"
                        defaultValue={entity.description || ""}
                        disabled={!isEditingSettings}
                        className="text-xs resize-none max-w-md"
                      />
                      <p className="text-xs text-muted-foreground">
                        A brief description of what this entity represents
                      </p>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="icon">Icon</Label>
                      <Input
                        id="icon"
                        defaultValue={entity.icon || ""}
                        disabled={!isEditingSettings}
                        placeholder="e.g., user, building, package"
                        className="max-w-md"
                      />
                    </div>

                    <div className="flex gap-2">
                      {isEditingSettings ? (
                        <>
                          <Button
                            onClick={async () => {
                              const displayNameInput = document.getElementById(
                                "display_name"
                              ) as HTMLInputElement
                              const descriptionInput = document.getElementById(
                                "description"
                              ) as HTMLTextAreaElement
                              const iconInput = document.getElementById(
                                "icon"
                              ) as HTMLInputElement

                              await updateEntityMutation({
                                display_name: displayNameInput.value,
                                description: descriptionInput.value,
                                icon: iconInput.value,
                              })
                            }}
                          >
                            Save changes
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => setIsEditingSettings(false)}
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
        onOpenChange={setCreateFieldDialogOpen}
        onSubmit={async (data) => {
          await createFieldMutation(data)
        }}
      />
    </div>
  )
}
