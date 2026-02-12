"use client"

import { BookOpenIcon, ChevronLeftIcon, GitBranchIcon } from "lucide-react"
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
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"

export function RegistrySidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const canAdministerOrg = useScopeCheck("org:registry:manage")
  const pathname = usePathname()

  const navMain = [
    {
      title: "Actions",
      url: "/registry/actions",
      icon: BookOpenIcon,
      isActive: pathname?.includes("/registry/actions"),
    },
    ...(canAdministerOrg
      ? // Only show repositories if the user can administer the org
        [
          {
            title: "Repositories",
            url: "/registry/repositories",
            icon: GitBranchIcon,
            isActive: pathname?.includes("/registry/repositories"),
          },
        ]
      : []),
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
      <SidebarFooter>
        <SidebarUserNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
