"use client"

import {
  BookOpenIcon,
  BuildingIcon,
  ChevronsUpDown,
  CircleCheck,
  Plus,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { useState } from "react"
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
              size="default"
              className="data-[state=open]:bg-zinc-100 dark:data-[state=open]:bg-zinc-900 pl-0"
            >
              <img src="/icon.png" alt="Tracecat" className="size-6 ml-0.5" />
              <span className="truncate font-semibold text-zinc-700 dark:text-zinc-300">
                {activeWorkspace?.name || "Select workspace"}
              </span>
              <ChevronsUpDown className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-[--radix-dropdown-menu-trigger-width] min-w-[220px] rounded-lg flex flex-col gap-1"
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
                  "gap-2 py-1 px-2",
                  workspace.id === workspaceId && "bg-zinc-100 dark:bg-zinc-800"
                )}
              >
                <div className="flex size-6 items-center justify-center rounded-md bg-muted text-[10px]">
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
            {user?.isPrivileged() && (
              <DropdownMenuItem
                className="gap-2 py-1 px-2"
                onClick={() => router.push("/organization")}
              >
                <div className="flex size-6 items-center justify-center">
                  <BuildingIcon className="size-4" />
                </div>
                <span>Organization</span>
              </DropdownMenuItem>
            )}
            <DropdownMenuItem
              className="gap-2 py-1 px-2"
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
