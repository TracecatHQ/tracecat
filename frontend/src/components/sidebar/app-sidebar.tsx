"use client"

import {
  BracesIcon,
  KeyRoundIcon,
  ShieldAlertIcon,
  Table2Icon,
  UsersIcon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
import { AppMenu } from "@/components/sidebar/app-menu"
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

function SidebarHeaderContent({ workspaceId }: { workspaceId: string }) {
  return <AppMenu workspaceId={workspaceId} />
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const { workspaceId } = useWorkspace()
  const basePath = `/workspaces/${workspaceId}`

  const navMain = [
    {
      title: "Workflows",
      url: `${basePath}/workflows`,
      icon: WorkflowIcon,
      isActive: pathname?.startsWith(`${basePath}/workflows`),
    },
    {
      title: "Cases",
      url: `${basePath}/cases`,
      icon: ShieldAlertIcon,
      isActive: pathname?.startsWith(`${basePath}/cases`),
    },
  ]

  const navWorkspace = [
    {
      title: "Tables",
      url: `${basePath}/tables`,
      icon: Table2Icon,
      isActive: pathname?.startsWith(`${basePath}/tables`),
    },
    {
      title: "Credentials",
      url: `${basePath}/credentials`,
      icon: KeyRoundIcon,
      isActive: pathname?.startsWith(`${basePath}/credentials`),
    },
    {
      title: "Integrations",
      url: `${basePath}/integrations`,
      icon: ZapIcon,
      isActive: pathname?.startsWith(`${basePath}/integrations`),
    },
    {
      title: "Members",
      url: `${basePath}/members`,
      icon: UsersIcon,
      isActive: pathname?.startsWith(`${basePath}/members`),
    },
    {
      title: "Custom fields",
      url: `${basePath}/custom-fields`,
      icon: BracesIcon,
      isActive: pathname?.startsWith(`${basePath}/custom-fields`),
    },
  ]

  return (
    <Sidebar collapsible="offcanvas" variant="inset" {...props}>
      <SidebarHeader>
        <SidebarHeaderContent workspaceId={workspaceId} />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navMain.map((item) => (
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
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navWorkspace.map((item) => (
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
