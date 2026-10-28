"use client"

import Cookies from "js-cookie"
import {
  BuildingIcon,
  ChevronsUpDown,
  CircleCheck,
  Plus,
  RadarIcon,
  SearchIcon,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useOrganization,
  useOrganizationMemberships,
} from "@/hooks/use-organization"
import { useWorkspaceManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { getWorkspaceLandingPath } from "@/lib/workspace-navigation"

/** Show the workspace search input once the list grows past this size. */
const WORKSPACE_SEARCH_THRESHOLD = 5

export function AppMenu({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const { workspaces, createWorkspace } = useWorkspaceManager()
  const { organization: activeOrganization } = useOrganization()
  const { organizations } = useOrganizationMemberships()
  const canAdministerOrg = useScopeCheck("org:update")
  const canManageScim = useScopeCheck("org:scim:manage")
  const canCreateWorkspace = useScopeCheck("workspace:create")
  const {
    hasEntitlement,
    hasEntitlementData,
    isLoading: entitlementsLoading,
  } = useEntitlements()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [workspaceName, setWorkspaceName] = useState("")
  const [isCreating, setIsCreating] = useState(false)
  const [workspaceSearch, setWorkspaceSearch] = useState("")

  // SCIM-only administrators land on SCIM, which needs the plan entitlement.
  const canOpenScim =
    canManageScim === true &&
    hasEntitlementData &&
    !entitlementsLoading &&
    hasEntitlement("rbac_addons")
  const activeWorkspace = workspaces?.find((ws) => ws.id === workspaceId)
  const showWorkspaceSearch =
    (workspaces?.length ?? 0) > WORKSPACE_SEARCH_THRESHOLD
  const normalizedSearch = workspaceSearch.trim().toLowerCase()
  const filteredWorkspaces = normalizedSearch
    ? workspaces?.filter((ws) =>
        ws.name.toLowerCase().includes(normalizedSearch)
      )
    : workspaces
  const showOrganizationSelector = (organizations?.length ?? 0) > 1
  const canCreateAdditionalWorkspace =
    canCreateWorkspace === true &&
    hasEntitlementData &&
    !entitlementsLoading &&
    hasEntitlement("multi_workspace")

  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canCreateAdditionalWorkspace || !workspaceName.trim()) return

    setIsCreating(true)
    try {
      const newWorkspace = await createWorkspace({ name: workspaceName.trim() })
      setDialogOpen(false)
      setWorkspaceName("")
      // Navigate to the new workspace
      router.push(getWorkspaceLandingPath(newWorkspace.id))
    } catch (error) {
      console.error("Failed to create workspace:", error)
    } finally {
      setIsCreating(false)
    }
  }

  const getWorkspaceInitials = (name: string) => {
    const words = name.trim().split(/\s+/)
    if (words.length === 1) {
      return words[0].substring(0, 2).toUpperCase()
    }
    return words
      .slice(0, 2)
      .map((word) => word[0])
      .join("")
      .toUpperCase()
  }

  const handleWorkspaceSearchKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement>
  ) => {
    if (e.key === "Escape") return
    if (e.key === "ArrowDown") {
      // Hand focus to the first menu item so arrow navigation continues.
      e.preventDefault()
      e.currentTarget
        .closest('[role="menu"]')
        ?.querySelector<HTMLElement>('[role="menuitem"]')
        ?.focus()
      return
    }
    // Keep Radix typeahead from stealing keystrokes meant for the input.
    e.stopPropagation()
    if (e.key === "Enter") {
      const match = filteredWorkspaces?.[0]
      if (match) {
        e.preventDefault()
        router.push(getWorkspaceLandingPath(match.id))
      }
    }
  }

  const handleSelectOrganization = (organizationId: string) => {
    if (organizationId === activeOrganization?.id) {
      return
    }

    Cookies.set("tracecat:active-org-id", organizationId, {
      sameSite: "lax",
      secure:
        typeof window !== "undefined" && window.location.protocol === "https:",
    })
    // Hard navigation: switching orgs invalidates all org-scoped client state
    // (React Query caches, providers), so re-bootstrap instead of router.push.
    window.location.assign("/workspaces")
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu
          onOpenChange={(open) => {
            if (!open) setWorkspaceSearch("")
          }}
        >
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="default"
              className="data-[state=open]:bg-foreground/5 dark:data-[state=open]:bg-foreground/10 pl-0"
            >
              <img src="/icon.png" alt="Tracecat" className="size-6 ml-0.5" />
              <span className="truncate font-semibold text-sidebar-foreground">
                {activeWorkspace?.name || "Select workspace"}
              </span>
              <ChevronsUpDown className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-[--radix-dropdown-menu-trigger-width] min-w-[220px] max-h-[50vh] rounded-lg flex flex-col gap-1 overflow-y-auto"
            align="start"
            side="right"
            sideOffset={4}
          >
            <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
              Workspaces
            </DropdownMenuLabel>
            {showWorkspaceSearch && (
              <div className="sticky top-0 z-10 bg-popover px-1 pb-1">
                <div className="relative">
                  <SearchIcon className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    autoFocus
                    value={workspaceSearch}
                    onChange={(e) => setWorkspaceSearch(e.target.value)}
                    onKeyDown={handleWorkspaceSearchKeyDown}
                    placeholder="Search workspaces..."
                    aria-label="Search workspaces"
                    className="h-8 pl-7 text-xs"
                  />
                </div>
              </div>
            )}
            {filteredWorkspaces?.length === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                No workspaces found
              </div>
            )}
            {filteredWorkspaces?.map((workspace) => (
              <DropdownMenuItem key={workspace.id} asChild>
                <Link
                  key={workspace.id}
                  href={getWorkspaceLandingPath(workspace.id)}
                  className={cn(
                    "flex items-center gap-2 py-1 px-2",
                    workspace.id === workspaceId &&
                      "bg-foreground/5 dark:bg-foreground/10"
                  )}
                >
                  <div className="flex size-6 items-center justify-center rounded-md bg-muted text-[10px]">
                    {getWorkspaceInitials(workspace.name)}
                  </div>
                  <span className="flex-1">{workspace.name}</span>
                  {workspace.id === workspaceId && (
                    <CircleCheck className="ml-auto size-4" />
                  )}
                </Link>
              </DropdownMenuItem>
            ))}
            {canCreateAdditionalWorkspace && (
              <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogTrigger asChild>
                  <DropdownMenuItem
                    className="gap-2 py-1 px-2"
                    onSelect={(e) => {
                      e.preventDefault()
                      setDialogOpen(true)
                    }}
                  >
                    <div className="flex size-6 items-center justify-center rounded-md border bg-background">
                      <Plus className="size-4" />
                    </div>
                    <div className="font-medium text-muted-foreground">
                      Create workspace
                    </div>
                  </DropdownMenuItem>
                </DialogTrigger>
                <DialogContent size="sm" className="sm:max-w-[425px]">
                  <DialogHeader>
                    <DialogTitle>Create workspace</DialogTitle>
                    <DialogDescription>
                      Workspaces are isolated environments where a team can work
                      on cases, automations, and credentials.
                    </DialogDescription>
                  </DialogHeader>
                  <form
                    onSubmit={handleCreateWorkspace}
                    className="flex min-h-0 flex-1 flex-col"
                  >
                    <DialogBody>
                      <div className="grid gap-4">
                        <div className="grid gap-2">
                          <Label htmlFor="workspace-name">Workspace name</Label>
                          <Input
                            id="workspace-name"
                            value={workspaceName}
                            onChange={(e) => setWorkspaceName(e.target.value)}
                            placeholder="My workspace"
                            disabled={isCreating}
                          />
                        </div>
                      </div>
                    </DialogBody>
                    <DialogFooter>
                      <Button
                        size="sm"
                        type="submit"
                        disabled={isCreating || !workspaceName.trim()}
                      >
                        {isCreating ? "Creating..." : "Create workspace"}
                      </Button>
                    </DialogFooter>
                  </form>
                </DialogContent>
              </Dialog>
            )}

            {showOrganizationSelector && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
                  Organizations
                </DropdownMenuLabel>
                {organizations?.map((organization) => (
                  <DropdownMenuItem
                    key={organization.id}
                    className={cn(
                      "flex items-center gap-2 py-1 px-2",
                      organization.id === activeOrganization?.id &&
                        "bg-foreground/5 dark:bg-foreground/10"
                    )}
                    onSelect={() => handleSelectOrganization(organization.id)}
                  >
                    <div className="flex size-6 items-center justify-center rounded-md bg-muted text-[10px]">
                      {getWorkspaceInitials(organization.name)}
                    </div>
                    <span className="flex-1">{organization.name}</span>
                    {organization.id === activeOrganization?.id && (
                      <CircleCheck className="ml-auto size-4" />
                    )}
                  </DropdownMenuItem>
                ))}
              </>
            )}

            <DropdownMenuSeparator />
            {(canAdministerOrg || canOpenScim) && (
              <DropdownMenuItem asChild>
                <Link
                  href={
                    canAdministerOrg
                      ? "/organization"
                      : "/organization/settings/scim"
                  }
                  className="flex items-center gap-2 py-1 px-2 cursor-default"
                >
                  <div className="flex size-6 items-center justify-center">
                    <BuildingIcon className="size-4" />
                  </div>
                  <span>Organization</span>
                </Link>
              </DropdownMenuItem>
            )}
            {canAdministerOrg && (
              <DropdownMenuItem asChild>
                <Link
                  href="/watchtower"
                  className="flex items-center gap-2 py-1 px-2 cursor-default"
                >
                  <div className="flex size-6 items-center justify-center">
                    <RadarIcon className="size-4" />
                  </div>
                  <span>Watchtower</span>
                </Link>
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
