"use client"

import Cookies from "js-cookie"
import {
  BookOpenIcon,
  BuildingIcon,
  ChevronLeftIcon,
  LayersIcon,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
import { useEffect } from "react"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useAppInfo } from "@/lib/hooks"

export function AdminSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const { appInfo } = useAppInfo()
  const multiTenantEnabled = appInfo?.ee_multi_tenant ?? true

  useEffect(() => {
    if (!multiTenantEnabled) {
      Cookies.remove("tracecat-org-id", { path: "/" })
      Cookies.remove("__tracecat:workspaces:last-viewed", { path: "/" })
    }
  }, [multiTenantEnabled])

  const navPlatform = multiTenantEnabled
    ? [
        {
          title: "Organizations",
          url: "/admin/organizations",
          icon: BuildingIcon,
          isActive: pathname?.includes("/admin/organizations"),
        },
        {
          title: "Users",
          url: "/admin/users",
          icon: UsersIcon,
          isActive: pathname?.includes("/admin/users"),
        },
        {
          title: "Tiers",
          url: "/admin/tiers",
          icon: LayersIcon,
          isActive: pathname?.includes("/admin/tiers"),
        },
      ]
    : []

  const navRegistry = [
    {
      title: "Repositories",
      url: "/admin/registry",
      isActive: pathname === "/admin/registry",
    },
    {
      title: "Versions",
      url: "/admin/registry/versions",
      isActive: pathname?.includes("/admin/registry/versions"),
    },
    {
      title: "Settings",
      url: "/admin/registry/settings",
      isActive: pathname?.includes("/admin/registry/settings"),
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
                <span>Exit admin console</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Platform</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navPlatform.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild isActive={item.isActive}>
                    <Link href={item.url}>
                      <item.icon />
                      <span>{item.title}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
              <SidebarMenuItem>
                <div className="flex w-full items-center gap-2 overflow-hidden rounded-md py-1.5 px-2 text-left text-[13px] text-zinc-700 dark:text-zinc-300">
                  <BookOpenIcon className="size-4 shrink-0" />
                  <span className="font-medium">Registry</span>
                </div>
                <SidebarMenuSub>
                  {navRegistry.map((subItem) => (
                    <SidebarMenuSubItem key={subItem.title}>
                      <SidebarMenuSubButton
                        asChild
                        isActive={subItem.isActive}
                        className="text-[13px]"
                      >
                        <Link href={subItem.url}>
                          <span>{subItem.title}</span>
                        </Link>
                      </SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                  ))}
                </SidebarMenuSub>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
