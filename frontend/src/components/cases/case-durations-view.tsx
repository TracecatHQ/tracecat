"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Timer } from "lucide-react"
import { useState } from "react"
import type { CaseDurationDefinitionUpdate } from "@/client"
import { CaseDurationsTable } from "@/components/cases/case-durations-table"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import {
  deleteCaseDurationDefinition,
  updateCaseDurationDefinition,
} from "@/lib/case-durations"
import { useCaseDurationDefinitions } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CaseDurationsView() {
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const queryClient = useQueryClient()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")

  const {
    caseDurationDefinitions,
    caseDurationDefinitionsIsLoading,
    caseDurationDefinitionsError,
  } = useCaseDurationDefinitions(workspaceId, caseAddonsEnabled)

  const { mutateAsync: handleDelete, isPending: deleteIsPending } = useMutation(
    {
      mutationFn: async (durationId: string) => {
        if (!workspaceId) {
          throw new Error("Workspace ID is required")
        }

        await deleteCaseDurationDefinition(workspaceId, durationId)
      },
      onSuccess: async () => {
        await queryClient.invalidateQueries({
          queryKey: ["case-duration-definitions", workspaceId],
        })
        toast({
          title: "Duration deleted",
          description: "The case duration definition was removed successfully.",
        })
      },
      onError: (error: unknown) => {
        console.error("Failed to delete case duration definition", error)
        toast({
          title: "Error deleting duration",
          description:
            error instanceof Error
              ? error.message
              : "Failed to delete the case duration definition. Please try again.",
          variant: "destructive",
        })
      },
    }
  )

  const [updatingDurationId, setUpdatingDurationId] = useState<string | null>(
    null
  )

  const { mutateAsync: handleUpdate, isPending: updateIsPending } = useMutation(
    {
      mutationFn: async ({
        durationId,
        payload,
      }: {
        durationId: string
        payload: CaseDurationDefinitionUpdate
      }) => {
        if (!workspaceId) {
          throw new Error("Workspace ID is required")
        }

        return await updateCaseDurationDefinition(
          workspaceId,
          durationId,
          payload
        )
      },
      onMutate: async ({ durationId }) => {
        setUpdatingDurationId(durationId)
      },
      onSuccess: async () => {
        if (!workspaceId) {
          return
        }

        await queryClient.invalidateQueries({
          queryKey: ["case-duration-definitions", workspaceId],
        })

        toast({
          title: "Duration updated",
          description: "The case duration definition was updated successfully.",
        })
      },
      onError: (error: unknown) => {
        console.error("Failed to update case duration definition", error)
        toast({
          title: "Error updating duration",
          description:
            error instanceof Error
              ? error.message
              : "Failed to update the case duration definition. Please try again.",
          variant: "destructive",
        })
      },
      onSettled: () => {
        setUpdatingDurationId(null)
      },
    }
  )

  // Check feature flag loading first - fastest check
  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  // Show enterprise-only message if feature is not enabled
  // This shows immediately after feature flags load (~200ms)
  if (!caseAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] items-center justify-center py-8">
          <EntitlementRequiredEmptyState
            title="Enterprise only"
            description="Case durations are only available on enterprise plans."
          >
            <Button
              variant="link"
              asChild
              className="text-muted-foreground"
              size="sm"
            >
              <a
                href="https://tracecat.com"
                target="_blank"
                rel="noopener noreferrer"
              >
                Learn more <ArrowUpRight className="size-4" />
              </a>
            </Button>
          </EntitlementRequiredEmptyState>
        </div>
      </div>
    )
  }

  // Only check workspace and case durations loading if feature is enabled
  if (workspaceLoading || caseDurationDefinitionsIsLoading) {
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

  if (caseDurationDefinitionsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading case duration definitions: ${caseDurationDefinitionsError.message}`}
      />
    )
  }

  if (!caseDurationDefinitions || caseDurationDefinitions.length === 0) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] items-center justify-center py-8">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Timer className="size-6" />
              </EmptyMedia>
              <EmptyTitle>No durations defined yet</EmptyTitle>
              <EmptyDescription>
                Add your first duration metric using the button in the header.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="flex h-full w-full flex-col space-y-8 p-8">
        <CaseDurationsTable
          durations={caseDurationDefinitions}
          onDeleteDuration={handleDelete}
          isDeleting={deleteIsPending}
          onUpdateDuration={async (durationId, payload) => {
            await handleUpdate({ durationId, payload })
          }}
          isUpdating={updateIsPending}
          updatingDurationId={updatingDurationId}
        />
      </div>
    </div>
  )
}
