"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { DatabaseIcon } from "lucide-react"
import {
  entitiesDeactivateEntityType,
  entitiesListFields,
  entitiesReactivateEntityType,
} from "@/client"
import { EntitiesTable } from "@/components/entities/entities-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { toast } from "@/components/ui/use-toast"
import { useEntities } from "@/lib/hooks/use-entities"
import { useWorkspace } from "@/providers/workspace"

export default function EntitiesPage() {
  const { workspaceId, workspace, workspaceError, workspaceLoading } =
    useWorkspace()
  const queryClient = useQueryClient()

  // Always show all entities including inactive ones
  const { entities, entitiesIsLoading, entitiesError } = useEntities(
    workspaceId,
    true // Always include inactive
  )

  // Fetch field counts for all entities
  const { data: fieldCounts = {} } = useQuery({
    queryKey: ["entity-field-counts", workspaceId, entities?.length],
    queryFn: async () => {
      if (!entities) return {}

      const counts: Record<string, number> = {}
      await Promise.all(
        entities.map(async (entity) => {
          try {
            const response = await entitiesListFields({
              workspaceId,
              entityId: entity.id,
              includeInactive: false,
            })
            counts[entity.id] = response.length
          } catch {
            counts[entity.id] = 0
          }
        })
      )
      return counts
    },
    enabled: !!entities && entities.length > 0,
  })

  const {
    mutateAsync: deactivateEntity,
    isPending: deactivateEntityIsPending,
  } = useMutation({
    mutationFn: async (entityId: string) => {
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
        queryKey: ["entity-field-counts", workspaceId],
      })
      toast({
        title: "Entity deactivated",
        description: "The entity was deactivated successfully.",
      })
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
    mutateAsync: reactivateEntity,
    isPending: reactivateEntityIsPending,
  } = useMutation({
    mutationFn: async (entityId: string) => {
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
        queryKey: ["entity-field-counts", workspaceId],
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

  const handleDeactivateEntity = async (entityId: string) => {
    await deactivateEntity(entityId)
  }

  const handleReactivateEntity = async (entityId: string) => {
    await reactivateEntity(entityId)
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
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        {entities.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-4">
            <div className="rounded-full bg-muted p-3">
              <DatabaseIcon className="size-8 text-muted-foreground" />
            </div>
            <div className="space-y-1 text-center">
              <h4 className="text-sm font-semibold text-muted-foreground">
                No custom entities defined yet
              </h4>
              <p className="text-xs text-muted-foreground">
                Create your first entity using the button in the header
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <EntitiesTable
              entities={entities}
              fieldCounts={fieldCounts}
              onDeactivateEntity={handleDeactivateEntity}
              onReactivateEntity={handleReactivateEntity}
              isDeleting={
                deactivateEntityIsPending || reactivateEntityIsPending
              }
            />
          </div>
        )}
      </div>
    </div>
  )
}
