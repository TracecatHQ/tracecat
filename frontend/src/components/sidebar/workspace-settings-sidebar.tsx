"use client"

import {
  BracesIcon,
  ChevronLeftIcon,
  CircleUserRoundIcon,
  KeyRoundIcon,
  Settings2,
  ShieldIcon,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
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
import { useWorkspace } from "@/providers/workspace"

export function WorkspaceSettingsSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const { workspaceId } = useWorkspace()
  const basePath = `/workspaces/${workspaceId}`

  const workspaceSettings = [
    {
      title: "General",
      url: `${basePath}/settings/general`,
      icon: Settings2,
      isActive:
        pathname?.endsWith("/settings/general") ||
        pathname?.endsWith("/settings"),
    },
    {
      title: "Credentials",
      url: `${basePath}/settings/credentials`,
      icon: KeyRoundIcon,
      isActive: pathname?.endsWith("/settings/credentials"),
    },
    {
      title: "Members",
      url: `${basePath}/settings/members`,
      icon: UsersIcon,
      isActive: pathname?.endsWith("/settings/members"),
    },
    {
      title: "Custom fields",
      url: `${basePath}/settings/custom-fields`,
      icon: BracesIcon,
      isActive: pathname?.endsWith("/settings/custom-fields"),
    },
  ]

  const accountSettings = [
    {
      title: "Profile",
      url: `${basePath}/settings/profile`,
      icon: CircleUserRoundIcon,
      isActive: pathname?.endsWith("/settings/profile"),
    },
    {
      title: "Security",
      url: `${basePath}/settings/security`,
      icon: ShieldIcon,
      isActive: pathname?.endsWith("/settings/security"),
    },
  ]

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <Link
                href={`${basePath}/workflows`}
                className="text-muted-foreground"
              >
                <ChevronLeftIcon />
                <span>Back to workspace</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {workspaceSettings.map((item) => (
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
          <SidebarGroupLabel>Account</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {accountSettings.map((item) => (
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
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
