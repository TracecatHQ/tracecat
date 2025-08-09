"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { DatabaseIcon } from "lucide-react"
import { entitiesDeactivateEntityType, entitiesListFields } from "@/client"
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

  const { entities, entitiesIsLoading, entitiesError } =
    useEntities(workspaceId)

  // Fetch field counts for all entities
  const { data: fieldCounts = {} } = useQuery({
    queryKey: ["entity-field-counts", workspaceId],
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

  const { mutateAsync: deleteEntity, isPending: deleteEntityIsPending } =
    useMutation({
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
          title: "Entity deleted",
          description: "The entity was deleted successfully.",
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

  const handleDeleteEntity = async (entityId: string) => {
    await deleteEntity(entityId)
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
              onDeleteEntity={handleDeleteEntity}
              isDeleting={deleteEntityIsPending}
            />
          </div>
        )}
      </div>
    </div>
  )
}
