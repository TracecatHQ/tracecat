"use client"

import { useQuery } from "@tanstack/react-query"
import { ArrowUpRight, Loader2 } from "lucide-react"
import { workspacesGetWorkspace } from "@/client"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import type { SettingsSection } from "@/components/settings/settings-modal-context"
import { WorkspaceFilesSettings } from "@/components/settings/workspace-files-settings"
import { WorkspaceGeneralSettings } from "@/components/settings/workspace-general-settings"
import { WorkspaceRuntimeSettings } from "@/components/settings/workspace-runtime-settings"
import { WorkspaceSyncSettings } from "@/components/settings/workspace-sync-settings"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

interface WorkspaceSettingsContainerProps {
  workspaceId: string
  activeSection: SettingsSection
}

export function WorkspaceSettingsContainer({
  workspaceId,
  activeSection,
}: WorkspaceSettingsContainerProps) {
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const {
    data: workspace,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: async () => workspacesGetWorkspace({ workspaceId }),
  })

  if (isLoading || entitlementsLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error || !workspace) {
    return (
      <div className="py-12 text-center text-sm text-muted-foreground">
        Failed to load workspace settings.
      </div>
    )
  }

  switch (activeSection) {
    case "workspace-general":
      return <WorkspaceGeneralSettings workspace={workspace} />
    case "workspace-runtime":
      return <WorkspaceRuntimeSettings workspace={workspace} />
    case "workspace-files":
      return <WorkspaceFilesSettings workspace={workspace} />
    case "workspace-sync":
      return hasEntitlement("git_sync") ? (
        <WorkspaceSyncSettings workspace={workspace} />
      ) : (
        <div className="flex flex-1 items-center justify-center py-12">
          <EntitlementRequiredEmptyState
            title="Upgrade required"
            description="Git sync is unavailable on your current plan."
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
      )
    default:
      return null
  }
}
