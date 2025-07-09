"use client"

import {
  BookOpenIcon,
  BuildingIcon,
  ChevronsUpDown,
  CircleCheck,
  KeyRoundIcon,
  Plus,
  Settings2,
  UsersIcon,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
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
import { useWorkspaceManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useAuth } from "@/providers/auth"

export function AppMenu({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const { workspaces, createWorkspace } = useWorkspaceManager()
  const { user } = useAuth()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [workspaceName, setWorkspaceName] = useState("")
  const [isCreating, setIsCreating] = useState(false)

  const activeWorkspace = workspaces?.find((ws) => ws.id === workspaceId)

  const handleWorkspaceChange = (newWorkspaceId: string) => {
    router.push(`/workspaces/${newWorkspaceId}/workflows`)
  }

  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!workspaceName.trim()) return

    setIsCreating(true)
    try {
      const newWorkspace = await createWorkspace({ name: workspaceName.trim() })
      setDialogOpen(false)
      setWorkspaceName("")
      // Navigate to the new workspace
      router.push(`/workspaces/${newWorkspace.id}/workflows`)
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

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
            >
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                <Icons.logo className="size-4" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">
                  {activeWorkspace?.name || "Select workspace"}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-[--radix-dropdown-menu-trigger-width] min-w-[220px] rounded-lg"
            align="start"
            side="right"
            sideOffset={4}
          >
            <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
              Workspaces
            </DropdownMenuLabel>
            {workspaces?.map((workspace, index) => (
              <DropdownMenuItem
                key={workspace.id}
                onClick={() => handleWorkspaceChange(workspace.id)}
                className={cn(
                  "gap-2 p-2",
                  workspace.id === workspaceId && "bg-sidebar-accent"
                )}
              >
                <div className="flex size-6 items-center justify-center rounded-md bg-muted text-[10px] font-medium">
                  {getWorkspaceInitials(workspace.name)}
                </div>
                <span className="flex-1">{workspace.name}</span>
                {workspace.id === workspaceId && (
                  <CircleCheck className="ml-auto size-4" />
                )}
              </DropdownMenuItem>
            ))}
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <DialogTrigger asChild>
                <DropdownMenuItem
                  className="gap-2 p-2"
                  onSelect={(e) => {
                    e.preventDefault()
                    setDialogOpen(true)
                  }}
                >
                  <div className="flex size-6 items-center justify-center rounded-md border bg-background">
                    <Plus className="size-4" />
                  </div>
                  <div className="font-medium text-muted-foreground">
                    Add workspace
                  </div>
                </DropdownMenuItem>
              </DialogTrigger>
              <DialogContent className="sm:max-w-[425px]">
                <form onSubmit={handleCreateWorkspace}>
                  <DialogHeader>
                    <DialogTitle>Create a new workspace</DialogTitle>
                    <DialogDescription>
                      Workspaces are isolated environments where a team can work
                      on cases, automations, and credentials.
                    </DialogDescription>
                  </DialogHeader>
                  <div className="grid gap-4 py-4">
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
                  <DialogFooter>
                    <DialogClose asChild>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={isCreating}
                      >
                        Cancel
                      </Button>
                    </DialogClose>
                    <Button
                      type="submit"
                      disabled={isCreating || !workspaceName.trim()}
                    >
                      {isCreating ? "Creating..." : "Create workspace"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>

            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="gap-2 p-2"
              onClick={() => router.push(`/workspaces/${workspaceId}/settings`)}
            >
              <div className="flex size-6 items-center justify-center">
                <Settings2 className="size-4" />
              </div>
              <span>Settings</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              className="gap-2 p-2"
              onClick={() =>
                router.push(`/workspaces/${workspaceId}/settings/credentials`)
              }
            >
              <div className="flex size-6 items-center justify-center">
                <KeyRoundIcon className="size-4" />
              </div>
              <span>Credentials</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              className="gap-2 p-2"
              onClick={() =>
                router.push(`/workspaces/${workspaceId}/settings/members`)
              }
            >
              <div className="flex size-6 items-center justify-center">
                <UsersIcon className="size-4" />
              </div>
              <span>Manage members</span>
            </DropdownMenuItem>

            <DropdownMenuSeparator />
            {user?.isPrivileged() && (
              <DropdownMenuItem
                className="gap-2 p-2"
                onClick={() => router.push("/organization")}
              >
                <div className="flex size-6 items-center justify-center">
                  <BuildingIcon className="size-4" />
                </div>
                <span>Organization</span>
              </DropdownMenuItem>
            )}
            <DropdownMenuItem
              className="gap-2 p-2"
              onClick={() => router.push("/registry/actions")}
            >
              <div className="flex size-6 items-center justify-center">
                <BookOpenIcon className="size-4" />
              </div>
              <span>Registry</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
