"use client"

import {
  BookOpenIcon,
  BuildingIcon,
  ChevronsUpDown,
  KeyRoundIcon,
  Plus,
  Settings2,
  UsersIcon,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { Icons } from "@/components/icons"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
  const { workspaces } = useWorkspaceManager()
  const { user } = useAuth()

  const activeWorkspace = workspaces?.find((ws) => ws.id === workspaceId)

  const handleWorkspaceChange = (newWorkspaceId: string) => {
    router.push(`/workspaces/${newWorkspaceId}/workflows`)
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
                <div className="flex size-6 items-center justify-center rounded-sm bg-sidebar-primary text-sidebar-primary-foreground">
                  <Icons.logo className="size-3 shrink-0" />
                </div>
                {workspace.name}
                <DropdownMenuShortcut>⌘{index + 1}</DropdownMenuShortcut>
              </DropdownMenuItem>
            ))}
            <DropdownMenuItem className="gap-2 p-2">
              <div className="flex size-6 items-center justify-center rounded-md border bg-background">
                <Plus className="size-4" />
              </div>
              <div className="font-medium text-muted-foreground">
                Add workspace
              </div>
            </DropdownMenuItem>

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
