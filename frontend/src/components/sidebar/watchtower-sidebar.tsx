"use client"

import {
  BoltIcon,
  ChevronLeftIcon,
  ComputerIcon,
  FileSearchIcon,
  PackageIcon,
  RadarIcon,
  ShieldIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"

export function WatchtowerSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()

  const navMain = [
    {
      title: "MCP Sessions",
      url: "/watchtower/mcp-connections",
      icon: RadarIcon,
      isActive: pathname?.startsWith("/watchtower/mcp-connections"),
    },
    {
      title: "Endpoints",
      url: "/watchtower/endpoints",
      icon: ComputerIcon,
      isActive: pathname?.startsWith("/watchtower/endpoints"),
    },
    {
      title: "Controls",
      url: "/watchtower/controls",
      icon: FileSearchIcon,
      isActive: pathname?.startsWith("/watchtower/controls"),
    },
    {
      title: "Actions",
      url: "/watchtower/actions",
      icon: BoltIcon,
      isActive: pathname?.startsWith("/watchtower/actions"),
    },
    {
      title: "Inventory",
      url: "/watchtower/inventory",
      icon: PackageIcon,
      isActive: pathname?.startsWith("/watchtower/inventory"),
    },
    {
      title: "Findings",
      url: "/watchtower/findings",
      icon: ShieldIcon,
      isActive: pathname?.startsWith("/watchtower/findings"),
    },
  ]

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
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
