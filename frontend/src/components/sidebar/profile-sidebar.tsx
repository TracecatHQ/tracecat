"use client"

import { ChevronLeftIcon, MailIcon, ShieldIcon, UserIcon } from "lucide-react"
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

export function ProfileSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()

  const navAccount = [
    {
      title: "General",
      url: "/profile/settings",
      icon: UserIcon,
      isActive: pathname === "/profile/settings" || pathname === "/profile",
    },
    {
      title: "Email",
      url: "/profile/email",
      icon: MailIcon,
      isActive: pathname === "/profile/email",
    },
    {
      title: "Security",
      url: "/profile/security",
      icon: ShieldIcon,
      isActive: pathname === "/profile/security",
    },
  ]

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
          <SidebarGroupLabel>Account settings</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navAccount.map((item) => (
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
