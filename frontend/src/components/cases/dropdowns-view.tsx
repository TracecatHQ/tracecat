"use client"

import { ArrowUpRight, ListIcon } from "lucide-react"
import { DropdownsTable } from "@/components/cases/dropdowns-table"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
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
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useCaseDropdownDefinitions } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function DropdownsView() {
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const { isFeatureEnabled, isLoading: featureFlagLoading } = useFeatureFlag()

  const {
    dropdownDefinitions,
    dropdownDefinitionsIsLoading,
    dropdownDefinitionsError,
    deleteDropdownDefinition,
    deleteDropdownDefinitionIsPending,
    updateDropdownDefinition,
    updateDropdownDefinitionIsPending,
    addDropdownOption,
    updateDropdownOption,
    deleteDropdownOption,
    reorderDropdownOptions,
  } = useCaseDropdownDefinitions(workspaceId)

  // Check feature flag loading first
  if (featureFlagLoading) {
    return <CenteredSpinner />
  }

  // Show enterprise-only message if feature is not enabled
  if (!isFeatureEnabled("case-dropdowns")) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] items-center justify-center py-8">
          <FeatureFlagEmptyState
            title="Enterprise only"
            description="Case dropdowns are only available on enterprise plans."
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
          </FeatureFlagEmptyState>
        </div>
      </div>
    )
  }

  if (workspaceLoading || dropdownDefinitionsIsLoading) {
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

  if (dropdownDefinitionsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading dropdowns: ${dropdownDefinitionsError.message}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        {!dropdownDefinitions || dropdownDefinitions.length === 0 ? (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ListIcon className="size-6" />
              </EmptyMedia>
              <EmptyTitle>No dropdowns defined yet</EmptyTitle>
              <EmptyDescription>
                Add your first dropdown using the button in the header
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="space-y-4">
            <DropdownsTable
              definitions={dropdownDefinitions}
              onDeleteDefinition={async (id) => {
                await deleteDropdownDefinition({
                  workspaceId,
                  definitionId: id,
                })
              }}
              onUpdateDefinition={async (id, params) => {
                await updateDropdownDefinition({
                  workspaceId,
                  definitionId: id,
                  requestBody: params,
                })
              }}
              onAddOption={async (definitionId, option) => {
                await addDropdownOption({
                  workspaceId,
                  definitionId,
                  requestBody: option,
                })
              }}
              onUpdateOption={async (definitionId, optionId, option) => {
                await updateDropdownOption({
                  workspaceId,
                  definitionId,
                  optionId,
                  requestBody: option,
                })
              }}
              onDeleteOption={async (definitionId, optionId) => {
                await deleteDropdownOption({
                  workspaceId,
                  definitionId,
                  optionId,
                })
              }}
              onReorderOptions={async (definitionId, optionIds) => {
                await reorderDropdownOptions({
                  workspaceId,
                  definitionId,
                  requestBody: optionIds,
                })
              }}
              isDeleting={deleteDropdownDefinitionIsPending}
              isUpdating={updateDropdownDefinitionIsPending}
            />
          </div>
        )}
      </div>
    </div>
  )
}
