"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type EntityRead,
  entitiesArchiveEntity,
  entitiesDeleteEntity,
  entitiesListAllFields,
  entitiesListAllRelations,
  entitiesRestoreEntity,
  entitiesUpdateEntity,
} from "@/client"
import { EntitiesTable } from "@/components/entities/entities-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { toast } from "@/components/ui/use-toast"
import { useLocalStorage } from "@/lib/hooks"
import { useEntities } from "@/lib/hooks/use-entities"
import { useWorkspace } from "@/providers/workspace"

export default function EntitiesPage() {
  const { workspaceId, workspace, workspaceError, workspaceLoading } =
    useWorkspace()
  const queryClient = useQueryClient()
  const [includeInactive] = useLocalStorage("entities-include-inactive", false)

  // Always show all entities including inactive ones
  const { entities, entitiesIsLoading, entitiesError } = useEntities(
    workspaceId,
    includeInactive
  )

  // Fetch field counts with a single list-all-fields request and aggregate
  const { data: fieldCounts = {} } = useQuery({
    queryKey: ["entity-field-counts", workspaceId, entities?.length],
    queryFn: async () => {
      if (!entities) return {}
      const fields = await entitiesListAllFields({
        workspaceId,
        includeInactive: false,
      })
      const counts: Record<string, number> = {}
      for (const f of fields) {
        counts[f.entity_id] = (counts[f.entity_id] || 0) + 1
      }
      for (const e of entities) {
        if (counts[e.id] == null) counts[e.id] = 0
      }
      return counts
    },
    enabled: !!entities && entities.length > 0,
  })

  // Fetch relation counts (outgoing relation definitions per entity)
  const { data: relationCounts = {} } = useQuery({
    queryKey: ["entity-relation-counts", workspaceId, entities?.length],
    queryFn: async () => {
      if (!entities) return {}

      // Use the existing list-all-relations endpoint and count by source entity
      const all = await entitiesListAllRelations({
        workspaceId,
        includeInactive: false,
      })

      const counts: Record<string, number> = {}
      for (const rel of all) {
        counts[rel.source_entity_id] = (counts[rel.source_entity_id] || 0) + 1
      }
      // Ensure all entities appear with at least 0
      for (const e of entities) {
        if (counts[e.id] == null) counts[e.id] = 0
      }
      return counts
    },
    enabled: !!entities && entities.length > 0,
  })

  const { mutateAsync: archiveEntity, isPending: archiveEntityIsPending } =
    useMutation({
      mutationFn: async (entityId: string) => {
        return await entitiesArchiveEntity({
          workspaceId,
          entityId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entities", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-field-counts", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-relation-counts", workspaceId],
        })
        toast({
          title: "Entity archived",
          description: "The entity was archived successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to archive entity", error)
        toast({
          title: "Error archiving entity",
          description: "Failed to archive the entity. Please try again.",
          variant: "destructive",
        })
      },
    })

  const { mutateAsync: restoreEntity, isPending: restoreEntityIsPending } =
    useMutation({
      mutationFn: async (entityId: string) => {
        return await entitiesRestoreEntity({
          workspaceId,
          entityId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entities", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-field-counts", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-relation-counts", workspaceId],
        })
        toast({
          title: "Entity restored",
          description: "The entity was restored successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to restore entity", error)
        toast({
          title: "Error restoring entity",
          description: "Failed to restore the entity. Please try again.",
          variant: "destructive",
        })
      },
    })

  const { mutateAsync: updateEntity, isPending: updateEntityIsPending } =
    useMutation({
      mutationFn: async ({
        entity,
        data,
      }: {
        entity: EntityRead
        data: {
          display_name: string
          description?: string
          icon?: string
        }
      }) => {
        return await entitiesUpdateEntity({
          workspaceId,
          entityId: entity.id,
          requestBody: data,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entities", workspaceId],
        })
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

  const { mutateAsync: deleteEntity, isPending: deleteEntityIsPending } =
    useMutation({
      mutationFn: async (entityId: string) => {
        return await entitiesDeleteEntity({
          workspaceId,
          entityId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["entities", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-field-counts", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["entity-relation-counts", workspaceId],
        })
        toast({
          title: "Entity deleted",
          description: "The entity and all its data were permanently deleted.",
        })
      },
      onError: (error) => {
        console.error("Failed to delete entity", error)
        toast({
          title: "Error deleting entity",
          description: "Failed to delete the entity. Please try again.",
          variant: "destructive",
        })
      },
    })

  const handleEditEntity = async (
    entity: EntityRead,
    data: {
      display_name: string
      description?: string
      icon?: string
    }
  ) => {
    await updateEntity({ entity, data })
  }

  const handleDeleteEntity = async (entityId: string) => {
    await deleteEntity(entityId)
  }

  const handleDeactivateEntity = async (entityId: string) => {
    await archiveEntity(entityId)
  }

  const handleReactivateEntity = async (entityId: string) => {
    await restoreEntity(entityId)
  }

  if (workspaceLoading || entitiesIsLoading) {
    return <CenteredSpinner />
  }

  if (workspaceError) {
    return (
      <AlertNotification
        level="error"
        message="Error loading workspace info."
      />
    )
  }

  if (!workspace) {
    return <AlertNotification level="error" message="Workspace not found." />
  }

  if (entitiesError || !entities) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading entities: ${entitiesError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container max-w-[1200px] my-8">
        <div className="space-y-4">
          <EntitiesTable
            entities={entities}
            fieldCounts={fieldCounts}
            relationCounts={relationCounts}
            onEditEntity={handleEditEntity}
            onDeleteEntity={handleDeleteEntity}
            onDeactivateEntity={handleDeactivateEntity}
            onReactivateEntity={handleReactivateEntity}
            isDeleting={
              archiveEntityIsPending ||
              restoreEntityIsPending ||
              deleteEntityIsPending
            }
            isUpdating={updateEntityIsPending}
          />
        </div>
      </div>
    </div>
  )
}
