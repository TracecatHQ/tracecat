"use client"

import {
  FileIcon,
  GitBranchIcon,
  LockIcon,
  LogOut,
  Settings2,
  UserIcon,
  WorkflowIcon,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { ProfileSettings } from "@/components/settings/profile-settings"
import {
  type SettingsSection,
  useSettingsModal,
} from "@/components/settings/settings-modal-context"
import { WorkspaceSettingsContainer } from "@/components/settings/workspace-settings-container"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Separator } from "@/components/ui/separator"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useAuthActions } from "@/hooks/use-auth"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useUserScopes, useWorkspaceManager } from "@/lib/hooks"
import { hasGrantedScope } from "@/lib/scopes"
import { cn } from "@/lib/utils"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

type WorkspaceCandidate = {
  id: string
  name: string
}

function compareWorkspaceCandidates(
  left: WorkspaceCandidate,
  right: WorkspaceCandidate
) {
  const nameCompare = left.name.localeCompare(right.name)
  if (nameCompare !== 0) {
    return nameCompare
  }
  return left.id.localeCompare(right.id)
}

interface NavItemProps {
  icon: React.ElementType
  label: string
  section: SettingsSection
  activeSection: SettingsSection
  onSelect: (section: SettingsSection) => void
  blocked?: boolean
}

function NavItem({
  icon: Icon,
  label,
  section,
  activeSection,
  onSelect,
  blocked = false,
}: NavItemProps) {
  const isActive = activeSection === section
  return (
    <button
      type="button"
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium",
        isActive
          ? "bg-muted"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
        blocked && !isActive && "opacity-70"
      )}
      onClick={() => onSelect(section)}
    >
      <Icon className="size-4" />
      {label}
      {blocked && <LockIcon className="ml-auto size-3.5 opacity-70" />}
    </button>
  )
}

function SettingsModalContent() {
  const { setOpen, activeSection, setActiveSection } = useSettingsModal()
  const { logout } = useAuthActions()
  const { hasEntitlement } = useEntitlements()

  const contextWorkspaceId = useOptionalWorkspaceId()
  const { clearLastWorkspaceId, getLastWorkspaceId, workspaces } =
    useWorkspaceManager()
  const lastWorkspaceId = getLastWorkspaceId()
  const lastViewedWorkspace = workspaces?.find(
    (workspace) => workspace.id === lastWorkspaceId
  )
  const orderedFallbackWorkspaces = useMemo(() => {
    if (!workspaces) {
      return []
    }

    const remainingWorkspaces = workspaces
      .filter((workspace) => workspace.id !== lastViewedWorkspace?.id)
      .sort(compareWorkspaceCandidates)

    return lastViewedWorkspace
      ? [lastViewedWorkspace, ...remainingWorkspaces]
      : remainingWorkspaces
  }, [lastViewedWorkspace, workspaces])
  const [fallbackWorkspaceIndex, setFallbackWorkspaceIndex] = useState(0)
  const fallbackWorkspace = contextWorkspaceId
    ? undefined
    : orderedFallbackWorkspaces[fallbackWorkspaceIndex]
  const workspaceId = contextWorkspaceId ?? fallbackWorkspace?.id

  useEffect(() => {
    setFallbackWorkspaceIndex(0)
  }, [contextWorkspaceId, orderedFallbackWorkspaces])

  useEffect(() => {
    if (
      contextWorkspaceId ||
      !lastWorkspaceId ||
      !workspaces ||
      lastViewedWorkspace
    ) {
      return
    }

    clearLastWorkspaceId()
  }, [
    clearLastWorkspaceId,
    contextWorkspaceId,
    lastViewedWorkspace,
    lastWorkspaceId,
    workspaces,
  ])

  const { userScopes, isLoading: scopesLoading } = useUserScopes(workspaceId, {
    enabled: !!workspaceId,
  })
  const canAdministerWorkspace =
    !scopesLoading &&
    hasGrantedScope("workspace:update", userScopes?.scopes ?? [])

  useEffect(() => {
    if (
      contextWorkspaceId ||
      !workspaceId ||
      scopesLoading ||
      canAdministerWorkspace ||
      fallbackWorkspaceIndex >= orderedFallbackWorkspaces.length - 1
    ) {
      return
    }

    setFallbackWorkspaceIndex((currentIndex) => currentIndex + 1)
  }, [
    canAdministerWorkspace,
    contextWorkspaceId,
    fallbackWorkspaceIndex,
    orderedFallbackWorkspaces.length,
    scopesLoading,
    workspaceId,
  ])

  const showWorkspaceNav = !!workspaceId && canAdministerWorkspace
  const canDisplaySection =
    activeSection === "profile" || canAdministerWorkspace
  const displayedSection =
    showWorkspaceNav && canDisplaySection ? activeSection : "profile"
  const showSyncNav = hasEntitlement("git_sync")

  return (
    <DialogContent className="h-[600px] max-w-[900px] gap-0 overflow-hidden p-0">
      <TooltipProvider>
        <DialogTitle className="sr-only">Settings</DialogTitle>
        <DialogDescription className="sr-only">
          Manage your account and workspace settings
        </DialogDescription>
        <div className="flex h-full">
          {/* Left nav panel */}
          <div className="flex w-[200px] shrink-0 flex-col border-r">
            <div className="flex flex-col gap-1 p-3">
              <span className="px-2 py-1 text-xs font-medium text-muted-foreground">
                Account
              </span>
              <NavItem
                icon={UserIcon}
                label="Profile"
                section="profile"
                activeSection={displayedSection}
                onSelect={setActiveSection}
              />

              {showWorkspaceNav && (
                <>
                  <span className="mt-3 px-2 py-1 text-xs font-medium text-muted-foreground">
                    Workspace
                  </span>
                  {canAdministerWorkspace && (
                    <>
                      <NavItem
                        icon={Settings2}
                        label="General"
                        section="workspace-general"
                        activeSection={displayedSection}
                        onSelect={setActiveSection}
                      />
                      <NavItem
                        icon={WorkflowIcon}
                        label="Workflows"
                        section="workspace-runtime"
                        activeSection={displayedSection}
                        onSelect={setActiveSection}
                      />
                      <NavItem
                        icon={FileIcon}
                        label="Files"
                        section="workspace-files"
                        activeSection={displayedSection}
                        onSelect={setActiveSection}
                      />
                      <NavItem
                        icon={GitBranchIcon}
                        label="Git sync"
                        section="workspace-sync"
                        activeSection={displayedSection}
                        onSelect={setActiveSection}
                        blocked={!showSyncNav}
                      />
                    </>
                  )}
                </>
              )}
            </div>
            <div className="mt-auto p-3">
              <Separator className="mb-3" />
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => {
                  setOpen(false)
                  logout()
                }}
              >
                <LogOut className="size-4" />
                Sign out
              </button>
            </div>
          </div>

          {/* Right content panel */}
          <div className="flex min-w-0 flex-1 flex-col gap-6 overflow-x-hidden overflow-y-auto p-8">
            {displayedSection === "profile" ? (
              <ProfileSettings />
            ) : workspaceId ? (
              <WorkspaceSettingsContainer
                workspaceId={workspaceId}
                activeSection={displayedSection}
              />
            ) : null}
          </div>
        </div>
      </TooltipProvider>
    </DialogContent>
  )
}

export function SettingsModal() {
  const { open, setOpen } = useSettingsModal()

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {open ? <SettingsModalContent /> : null}
    </Dialog>
  )
}
