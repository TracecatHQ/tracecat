"use client"

import {
  BotIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  GitBranchIcon,
  KeyRoundIcon,
  LockIcon,
  LogInIcon,
  LogsIcon,
  MailIcon,
  SendIcon,
  Settings2,
  ShieldIcon,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceManager } from "@/lib/hooks"

export function OrganizationSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const { isFeatureEnabled } = useFeatureFlag()

  // Fetch workspaces for the sidebar
  const { workspaces } = useWorkspaceManager()

  // Scope checks for org sidebar items
  const canViewSettings = useScopeCheck("org:settings:read")
  const canViewMembers = useScopeCheck("org:member:read")

  const navSettings = [
    {
      title: "Git repository",
      url: "/organization/settings/git",
      icon: GitBranchIcon,
      isActive: pathname?.includes("/organization/settings/git"),
      visible: canViewSettings !== false,
    },
    {
      title: "Single sign-on",
      url: "/organization/settings/sso",
      icon: LockIcon,
      isActive: pathname?.includes("/organization/settings/sso"),
      visible: canViewSettings !== false,
    },
    {
      title: "OAuth",
      url: "/organization/settings/oauth",
      icon: ShieldIcon,
      isActive: pathname?.includes("/organization/settings/oauth"),
      visible: canViewSettings !== false,
    },
    {
      title: "Email authentication",
      url: "/organization/settings/auth",
      icon: MailIcon,
      isActive: pathname?.includes("/organization/settings/auth"),
      visible: canViewSettings !== false,
    },
    {
      title: "Application",
      url: "/organization/settings/app",
      icon: Settings2,
      isActive: pathname?.includes("/organization/settings/app"),
      visible: canViewSettings !== false,
    },
    {
      title: "Audit Logs",
      url: "/organization/settings/audit",
      icon: LogsIcon,
      isActive: pathname?.includes("/organization/settings/audit"),
      visible: canViewSettings !== false,
    },
    {
      title: "Agent",
      url: "/organization/settings/agent",
      icon: BotIcon,
      isActive: pathname?.includes("/organization/settings/agent"),
      visible: canViewSettings !== false,
    },
    ...(isFeatureEnabled("git-sync")
      ? [
          {
            title: "Workflow sync",
            url: "/organization/vcs",
            icon: GitBranchIcon,
            isActive: pathname?.includes("/organization/vcs"),
            visible: canViewSettings !== false,
          },
        ]
      : []),
  ]

  const navSecrets = [
    {
      title: "SSH keys",
      url: "/organization/ssh-keys",
      icon: KeyRoundIcon,
      isActive: pathname?.includes("/organization/ssh-keys"),
      visible: canViewSettings !== false,
    },
  ]

  const navUsers = [
    {
      title: "Members",
      url: "/organization/members",
      icon: UsersIcon,
      isActive: pathname?.includes("/organization/members"),
      visible: canViewMembers !== false,
    },
    {
      title: "Invitations",
      url: "/organization/invitations",
      icon: SendIcon,
      isActive: pathname?.includes("/organization/invitations"),
      visible: canViewMembers !== false,
    },
    {
      title: "Sessions",
      url: "/organization/sessions",
      icon: LogInIcon,
      isActive: pathname?.includes("/organization/sessions"),
      visible: canViewMembers !== false,
    },
  ]

  // Helper function to get workspace initials
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
    <Sidebar collapsible="offcanvas" variant="inset" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <Link href="/workspaces" className="text-muted-foreground">
                <ChevronLeftIcon />
                <span>Back to workspaces</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        {navSettings.some((item) => item.visible !== false) && (
          <SidebarGroup>
            <SidebarGroupLabel>Settings</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {navSettings
                  .filter((item) => item.visible !== false)
                  .map((item) => (
                    <SidebarMenuItem key={item.title}>
                      <SidebarMenuButton asChild isActive={item.isActive}>
                        <Link href={item.url}>
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        {navSecrets.some((item) => item.visible !== false) && (
          <SidebarGroup>
            <SidebarGroupLabel>Secrets</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {navSecrets
                  .filter((item) => item.visible !== false)
                  .map((item) => (
                    <SidebarMenuItem key={item.title}>
                      <SidebarMenuButton asChild isActive={item.isActive}>
                        <Link href={item.url}>
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        {navUsers.some((item) => item.visible !== false) && (
          <SidebarGroup>
            <SidebarGroupLabel>Users</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {navUsers
                  .filter((item) => item.visible !== false)
                  .map((item) => (
                    <SidebarMenuItem key={item.title}>
                      <SidebarMenuButton asChild isActive={item.isActive}>
                        <Link href={item.url}>
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        {workspaces && workspaces.length > 0 && (
          <SidebarGroup>
            <SidebarGroupLabel>Your workspaces</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {workspaces.map((workspace) => (
                  <SidebarMenuItem key={workspace.id}>
                    <SidebarMenuButton
                      asChild
                      isActive={pathname?.includes(
                        `/organization/settings/workspaces/${workspace.id}`
                      )}
                    >
                      <Link
                        href={`/organization/settings/workspaces/${workspace.id}`}
                      >
                        <div className="flex size-5 items-center justify-center rounded bg-muted text-[10px] font-medium">
                          {getWorkspaceInitials(workspace.name)}
                        </div>
                        <span>{workspace.name}</span>
                        <ChevronRightIcon className="ml-auto size-4 opacity-50" />
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
