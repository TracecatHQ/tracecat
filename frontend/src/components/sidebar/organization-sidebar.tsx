"use client"

import Cookies from "js-cookie"
import {
  BotIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  GitBranchIcon,
  KeyRoundIcon,
  LockIcon,
  LogInIcon,
  LogOutIcon,
  LogsIcon,
  MailIcon,
  SendIcon,
  Settings2,
  ShieldIcon,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import type * as React from "react"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import { Button } from "@/components/ui/button"
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
import { useAuth } from "@/hooks/use-auth"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceManager } from "@/lib/hooks"

const ORG_OVERRIDE_COOKIE = "tracecat-org-id"

export function OrganizationSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const router = useRouter()
  const { isFeatureEnabled } = useFeatureFlag()
  const { user } = useAuth()

  // Check if superuser is in org override mode
  const orgOverrideCookie = Cookies.get(ORG_OVERRIDE_COOKIE)
  const isInOrgOverrideMode = user?.isSuperuser && !!orgOverrideCookie

  const handleExitOrgContext = () => {
    Cookies.remove(ORG_OVERRIDE_COOKIE, { path: "/" })
    router.push("/admin/organizations")
  }

  // Fetch workspaces for the sidebar
  const { workspaces } = useWorkspaceManager()

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
    {
      title: "Audit Logs",
      url: "/organization/settings/audit",
      icon: LogsIcon,
      isActive: pathname?.includes("/organization/settings/audit"),
    },
    {
      title: "Agent",
      url: "/organization/settings/agent",
      icon: BotIcon,
      isActive: pathname?.includes("/organization/settings/agent"),
    },
    ...(isFeatureEnabled("git-sync")
      ? [
          {
            title: "Workflow sync",
            url: "/organization/vcs",
            icon: GitBranchIcon,
            isActive: pathname?.includes("/organization/vcs"),
          },
        ]
      : []),
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
      title: "Invitations",
      url: "/organization/invitations",
      icon: SendIcon,
      isActive: pathname?.includes("/organization/invitations"),
    },
    {
      title: "Sessions",
      url: "/organization/sessions",
      icon: LogInIcon,
      isActive: pathname?.includes("/organization/sessions"),
    },
  ]

  // Helper function to get workspace initials
  const getWorkspaceInitials = (name: string) => {
    const words = name.trim().split(/\s+/)
    if (words.length === 1) {
      return words[0].substring(0, 2).toUpperCase()
    }
    return words
      .slice(0, 2)
      .map((word) => word[0])
      .join("")
      .toUpperCase()
  }

  return (
    <Sidebar collapsible="offcanvas" variant="inset" {...props}>
      <SidebarHeader>
        {isInOrgOverrideMode && (
          <div className="flex items-center justify-between gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2">
            <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
              Viewing as admin
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs text-amber-600 hover:bg-amber-500/20 hover:text-amber-700 dark:text-amber-400 dark:hover:text-amber-300"
              onClick={handleExitOrgContext}
            >
              <LogOutIcon className="mr-1 size-3" />
              Exit
            </Button>
          </div>
        )}
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

        {workspaces && workspaces.length > 0 && (
          <SidebarGroup>
            <SidebarGroupLabel>Your workspaces</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {workspaces.map((workspace) => (
                  <SidebarMenuItem key={workspace.id}>
                    <SidebarMenuButton
                      asChild
                      isActive={pathname?.includes(
                        `/organization/settings/workspaces/${workspace.id}`
                      )}
                    >
                      <Link
                        href={`/organization/settings/workspaces/${workspace.id}`}
                      >
                        <div className="flex size-5 items-center justify-center rounded bg-muted text-[10px] font-medium">
                          {getWorkspaceInitials(workspace.name)}
                        </div>
                        <span>{workspace.name}</span>
                        <ChevronRightIcon className="ml-auto size-4 opacity-50" />
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
