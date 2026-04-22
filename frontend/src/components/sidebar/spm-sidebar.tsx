"use client"

import {
  ChevronLeftIcon,
  ComputerIcon,
  FileSearchIcon,
  PackageIcon,
  ShieldIcon,
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
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"

/**
 * Sidebar navigation for the SPM operator route family.
 */
export function SpmSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const navItems = [
    {
      title: "Endpoints",
      url: "/spm/endpoints",
      icon: ComputerIcon,
      isActive: pathname?.startsWith("/spm/endpoints"),
    },
    {
      title: "Findings",
      url: "/spm/findings",
      icon: ShieldIcon,
      isActive: pathname?.startsWith("/spm/findings"),
    },
    {
      title: "Assets",
      url: "/spm/assets",
      icon: PackageIcon,
      isActive: pathname?.startsWith("/spm/assets"),
    },
    {
      title: "Controls",
      url: "/spm/controls",
      icon: FileSearchIcon,
      isActive: pathname?.startsWith("/spm/controls"),
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
              {navItems.map((item) => (
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
