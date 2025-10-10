"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Timer } from "lucide-react"
import { CaseDurationsTable } from "@/components/cases/case-durations-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { deleteCaseDuration } from "@/lib/case-durations"
import { useCaseDurations } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CaseDurationsView() {
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const queryClient = useQueryClient()

  const { caseDurations, caseDurationsIsLoading, caseDurationsError } =
    useCaseDurations(workspaceId)

  const { mutateAsync: handleDelete, isPending: deleteIsPending } = useMutation(
    {
      mutationFn: async (durationId: string) => {
        if (!workspaceId) {
          throw new Error("Workspace ID is required")
        }

        await deleteCaseDuration(workspaceId, durationId)
      },
      onSuccess: async () => {
        await queryClient.invalidateQueries({
          queryKey: ["case-durations", workspaceId],
        })
        toast({
          title: "Duration deleted",
          description: "The case duration was removed successfully.",
        })
      },
      onError: (error: unknown) => {
        console.error("Failed to delete case duration", error)
        toast({
          title: "Error deleting duration",
          description:
            error instanceof Error
              ? error.message
              : "Failed to delete the case duration. Please try again.",
          variant: "destructive",
        })
      },
    }
  )

  if (workspaceLoading || caseDurationsIsLoading) {
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

  if (caseDurationsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading case durations: ${caseDurationsError.message}`}
      />
    )
  }

  if (!caseDurations || caseDurations.length === 0) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] flex-col items-center justify-center space-y-4 py-8 text-center">
          <div className="rounded-full bg-muted p-3">
            <Timer className="size-8 text-muted-foreground" />
          </div>
          <div className="space-y-1 text-muted-foreground">
            <h4 className="text-sm font-semibold">No durations defined yet</h4>
            <p className="text-xs">
              Add your first duration metric using the button in the header.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        <CaseDurationsTable
          durations={caseDurations}
          onDeleteDuration={handleDelete}
          isDeleting={deleteIsPending}
        />
      </div>
    </div>
  )
}
