"use client"

import {
  ChevronLeftIcon,
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

  return (
    <Sidebar collapsible="icon" {...props}>
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
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
