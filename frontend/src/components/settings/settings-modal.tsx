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
import { useEffect } from "react"
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
  const fallbackWorkspace = workspaces?.find(
    (workspace) => workspace.id === lastWorkspaceId
  )
  const workspaceId =
    contextWorkspaceId ?? fallbackWorkspace?.id ?? workspaces?.[0]?.id

  useEffect(() => {
    if (
      contextWorkspaceId ||
      !lastWorkspaceId ||
      !workspaces ||
      fallbackWorkspace
    ) {
      return
    }

    clearLastWorkspaceId()
  }, [
    clearLastWorkspaceId,
    contextWorkspaceId,
    fallbackWorkspace,
    lastWorkspaceId,
    workspaces,
  ])

  const { userScopes, isLoading: scopesLoading } = useUserScopes(workspaceId, {
    enabled: !!workspaceId,
  })
  const canAdministerWorkspace =
    !scopesLoading &&
    hasGrantedScope("workspace:update", userScopes?.scopes ?? [])

  const showWorkspaceSection = !!workspaceId && canAdministerWorkspace
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
                activeSection={activeSection}
                onSelect={setActiveSection}
              />

              {showWorkspaceSection && (
                <>
                  <span className="mt-3 px-2 py-1 text-xs font-medium text-muted-foreground">
                    Workspace
                  </span>
                  <NavItem
                    icon={Settings2}
                    label="General"
                    section="workspace-general"
                    activeSection={activeSection}
                    onSelect={setActiveSection}
                  />
                  <NavItem
                    icon={WorkflowIcon}
                    label="Workflows"
                    section="workspace-runtime"
                    activeSection={activeSection}
                    onSelect={setActiveSection}
                  />
                  <NavItem
                    icon={FileIcon}
                    label="Files"
                    section="workspace-files"
                    activeSection={activeSection}
                    onSelect={setActiveSection}
                  />
                  <NavItem
                    icon={GitBranchIcon}
                    label="Git sync"
                    section="workspace-sync"
                    activeSection={activeSection}
                    onSelect={setActiveSection}
                    blocked={!showSyncNav}
                  />
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
          <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-8">
            {activeSection === "profile" ? (
              <ProfileSettings />
            ) : workspaceId ? (
              <WorkspaceSettingsContainer
                workspaceId={workspaceId}
                activeSection={activeSection}
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
