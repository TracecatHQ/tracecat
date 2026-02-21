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
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useOrgMembership } from "@/hooks/use-org-membership"

export function RegistrySidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const canReadRegistry = useScopeCheck("org:registry:read")
  const { canAdministerOrg } = useOrgMembership()
  const { hasEntitlement } = useEntitlements()
  const pathname = usePathname()
  const customRegistryEnabled = hasEntitlement("custom_registry")

  const navMain = [
    ...(canReadRegistry
      ? [
          {
            title: "Actions",
            url: "/registry/actions",
            icon: BookOpenIcon,
            isActive: pathname?.includes("/registry/actions"),
            locked: false,
          },
          ...(canAdministerOrg
            ? [
                {
                  title: "Repositories",
                  url: "/registry/repositories",
                  icon: GitBranchIcon,
                  isActive: pathname?.includes("/registry/repositories"),
                  locked: !customRegistryEnabled,
                },
              ]
            : []),
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
                  {item.locked ? (
                    <SidebarMenuBadge>Requires upgrade</SidebarMenuBadge>
                  ) : null}
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
