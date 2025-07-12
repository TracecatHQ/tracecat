"use client"

import { useQuery } from "@tanstack/react-query"
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  GitBranchIcon,
  KeyRoundIcon,
  LockIcon,
  LogInIcon,
  MailIcon,
  Settings2,
  ShieldIcon,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
import { workspacesListWorkspaces } from "@/client"
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

export function OrganizationSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()

  // Fetch workspaces for the sidebar
  const { data: workspaces } = useQuery({
    queryKey: ["workspaces"],
    queryFn: async () => await workspacesListWorkspaces(),
  })

  const navSettings = [
    {
      title: "Git repository",
      url: "/organization/settings/git",
      icon: GitBranchIcon,
      isActive: pathname?.includes("/organization/settings/git"),
    },
    {
      title: "Single sign-on",
      url: "/organization/settings/sso",
      icon: LockIcon,
      isActive: pathname?.includes("/organization/settings/sso"),
    },
    {
      title: "OAuth",
      url: "/organization/settings/oauth",
      icon: ShieldIcon,
      isActive: pathname?.includes("/organization/settings/oauth"),
    },
    {
      title: "Email authentication",
      url: "/organization/settings/auth",
      icon: MailIcon,
      isActive: pathname?.includes("/organization/settings/auth"),
    },
    {
      title: "Application",
      url: "/organization/settings/app",
      icon: Settings2,
      isActive: pathname?.includes("/organization/settings/app"),
    },
  ]

  const navSecrets = [
    {
      title: "SSH keys",
      url: "/organization/ssh-keys",
      icon: KeyRoundIcon,
      isActive: pathname?.includes("/organization/ssh-keys"),
    },
  ]

  const navUsers = [
    {
      title: "Members",
      url: "/organization/members",
      icon: UsersIcon,
      isActive: pathname?.includes("/organization/members"),
    },
    {
      title: "Sessions",
      url: "/organization/sessions",
      icon: LogInIcon,
      isActive: pathname?.includes("/organization/sessions"),
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
    <Sidebar collapsible="icon" variant="inset" {...props}>
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
        <SidebarGroup>
          <SidebarGroupLabel>Settings</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navSettings.map((item) => (
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

        <SidebarGroup>
          <SidebarGroupLabel>Secrets</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navSecrets.map((item) => (
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

        <SidebarGroup>
          <SidebarGroupLabel>Users</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navUsers.map((item) => (
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
