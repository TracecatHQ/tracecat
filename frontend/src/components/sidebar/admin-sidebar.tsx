"use client"

import Cookies from "js-cookie"
import {
  BookOpenIcon,
  BuildingIcon,
  ChevronLeftIcon,
  CrownIcon,
  LayersIcon,
  LogOutIcon,
  Settings2Icon,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import type * as React from "react"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import { Separator } from "@/components/ui/separator"
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
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useAuth } from "@/hooks/use-auth"

const ORG_OVERRIDE_COOKIE = "tracecat-org-id"

export function AdminSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const router = useRouter()
  const { user } = useAuth()

  const orgOverrideCookie = Cookies.get(ORG_OVERRIDE_COOKIE)
  const isInOrgOverrideMode = user?.isSuperuser && !!orgOverrideCookie

  function handleExitOrgContext() {
    Cookies.remove(ORG_OVERRIDE_COOKIE, { path: "/" })
    router.push("/admin/organizations")
  }

  const navPlatform = [
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

  const navSettings = [
    {
      title: "Settings",
      url: "/admin/settings",
      icon: Settings2Icon,
      isActive: pathname?.includes("/admin/settings"),
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
        <div className="flex items-center gap-2 px-2 py-1.5">
          <div className="flex size-6 items-center justify-center rounded bg-muted">
            <CrownIcon className="size-4" />
          </div>
          <span className="font-semibold">Admin</span>
        </div>
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

        <SidebarGroup>
          <SidebarGroupLabel>Configuration</SidebarGroupLabel>
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
      </SidebarContent>
      <SidebarFooter>
        {isInOrgOverrideMode ? (
          <SidebarMenu>
            <Separator className="my-1" />
            <SidebarMenuItem>
              <SidebarMenuButton onClick={handleExitOrgContext}>
                <LogOutIcon />
                <span>Exit org context</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        ) : (
          <SidebarUserNav />
        )}
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
