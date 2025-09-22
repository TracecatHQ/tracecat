"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type EntityRead,
  entitiesActivateEntity,
  entitiesDeactivateEntity,
  entitiesDeleteEntity,
  entitiesListFields,
  entitiesUpdateEntity,
} from "@/client"
import { EntitiesTable } from "@/components/entities/entities-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { toast } from "@/components/ui/use-toast"
import { useEntities } from "@/hooks/use-entities"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function EntitiesPage() {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const [includeInactive] = useLocalStorage("entities-include-inactive", false)

  const { entities, entitiesIsLoading, entitiesError } = useEntities(
    workspaceId,
    includeInactive
  )

  // Compute field counts by fetching fields per entity (best-effort)
  const { data: fieldCounts = {} } = useQuery({
    queryKey: ["entity-field-counts", workspaceId, entities?.length],
    queryFn: async () => {
      if (!entities || entities.length === 0) return {}
      const counts: Record<string, number> = {}
      await Promise.all(
        entities.map(async (e) => {
          try {
            const fields = await entitiesListFields({
              workspaceId,
              entityId: e.id,
              includeInactive: false,
            })
            counts[e.id] = fields.length
          } catch (_err) {
            counts[e.id] = 0
          }
        })
      )
      return counts
    },
    enabled: !!entities && entities.length > 0,
  })

  const { mutateAsync: deactivateEntity, isPending: deactivatePending } =
    useMutation({
      mutationFn: async (entityId: string) =>
        await entitiesDeactivateEntity({ workspaceId, entityId }),
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["entities", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["entity-field-counts", workspaceId],
        })
        toast({
          title: "Entity archived",
          description: "Successfully archived entity.",
        })
      },
      onError: (error) => {
        console.error("Failed to archive entity", error)
        toast({
          title: "Error archiving entity",
          description: "Failed to archive the entity. Please try again.",
        })
      },
    })

  const { mutateAsync: activateEntity, isPending: activatePending } =
    useMutation({
      mutationFn: async (entityId: string) =>
        await entitiesActivateEntity({ workspaceId, entityId }),
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["entities", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["entity-field-counts", workspaceId],
        })
        toast({
          title: "Entity restored",
          description: "Successfully restored entity.",
        })
      },
      onError: (error) => {
        console.error("Failed to restore entity", error)
        toast({
          title: "Error restoring entity",
          description: "Failed to restore the entity. Please try again.",
        })
      },
    })

  const { mutateAsync: deleteEntity, isPending: deletePending } = useMutation({
    mutationFn: async (entityId: string) =>
      await entitiesDeleteEntity({ workspaceId, entityId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["entities", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["entity-field-counts", workspaceId],
      })
      toast({
        title: "Entity deleted",
        description: "Successfully deleted entity.",
      })
    },
    onError: (error) => {
      console.error("Failed to delete entity", error)
      toast({
        title: "Error deleting entity",
        description: "Failed to delete the entity. Please try again.",
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
        data: { display_name: string; description?: string; icon?: string }
      }) => {
        return await entitiesUpdateEntity({
          workspaceId,
          entityId: entity.id,
          requestBody: {
            display_name: data.display_name,
            description: data.description ?? null,
            icon: data.icon ?? null,
          },
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["entities", workspaceId] })
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
        })
      },
    })

  if (entitiesIsLoading) return <CenteredSpinner />
  if (entitiesError)
    return <AlertNotification level="error" message={entitiesError.message} />

  return (
    <div className="size-full overflow-auto">
      <div className="container my-8 max-w-[1200px]">
        <div className="space-y-4">
          <EntitiesTable
            entities={entities ?? []}
            fieldCounts={fieldCounts}
            onEditEntity={(entity, data) => updateEntity({ entity, data })}
            onDeleteEntity={deleteEntity}
            onDeactivateEntity={deactivateEntity}
            onReactivateEntity={activateEntity}
            isDeleting={deactivatePending || activatePending || deletePending}
            isUpdating={updateEntityIsPending}
          />
        </div>
      </div>
    </div>
  )
}
