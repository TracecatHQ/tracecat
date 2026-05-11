"use client"

import {
  BotIcon,
  ChevronLeftIcon,
  GitBranchIcon,
  GlobeIcon,
  KeyRoundIcon,
  LockIcon,
  LogInIcon,
  LogsIcon,
  Settings2,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useEntitlements } from "@/hooks/use-entitlements"

export function OrganizationSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const { hasEntitlement } = useEntitlements()
  const customRegistryEnabled = hasEntitlement("custom_registry")
  const gitSyncEnabled = hasEntitlement("git_sync")
  const serviceAccountsEnabled = hasEntitlement("service_accounts")

  // Scope checks for org sidebar items
  const canViewSettings = useScopeCheck("org:settings:read")
  const canViewServiceAccounts = useScopeCheck("org:service_account:read")
  const canViewMembers = useScopeCheck("org:member:read")

  const navSettings = [
    {
      title: "Custom registry",
      url: "/organization/settings/custom-registry",
      icon: GitBranchIcon,
      isActive: pathname?.includes("/organization/settings/custom-registry"),
      visible: canViewSettings === true,
      locked: !customRegistryEnabled,
    },
    {
      title: "SAML (SSO)",
      url: "/organization/settings/sso",
      icon: LockIcon,
      isActive: pathname?.includes("/organization/settings/sso"),
      visible: canViewSettings === true,
      locked: false,
    },
    {
      title: "Domains",
      url: "/organization/settings/domains",
      icon: GlobeIcon,
      isActive: pathname?.includes("/organization/settings/domains"),
      visible: canViewSettings === true,
      locked: false,
    },
    {
      title: "Application",
      url: "/organization/settings/app",
      icon: Settings2,
      isActive: pathname?.includes("/organization/settings/app"),
      visible: canViewSettings === true,
      locked: false,
    },
    {
      title: "Audit Logs",
      url: "/organization/settings/audit",
      icon: LogsIcon,
      isActive: pathname?.includes("/organization/settings/audit"),
      visible: canViewSettings === true,
      locked: false,
    },
    {
      title: "Agent",
      url: "/organization/settings/agent",
      icon: BotIcon,
      isActive: pathname?.includes("/organization/settings/agent"),
      visible: canViewSettings === true,
      locked: false,
    },
    {
      title: "Git sync",
      url: "/organization/vcs",
      icon: GitBranchIcon,
      isActive: pathname?.includes("/organization/vcs"),
      visible: canViewSettings === true,
      locked: !gitSyncEnabled,
    },
    // MCP settings page not yet implemented — hidden until route exists
    // {
    //   title: "MCP",
    //   url: "/organization/settings/mcp",
    //   icon: LinkIcon,
    //   isActive: pathname?.includes("/organization/settings/mcp"),
    // },
  ]

  const navSecrets = [
    {
      title: "SSH keys",
      url: "/organization/ssh-keys",
      icon: KeyRoundIcon,
      isActive: pathname?.includes("/organization/ssh-keys"),
      visible: canViewSettings === true,
    },
  ]

  const navManage = [
    {
      title: "Members",
      url: "/organization/members",
      icon: UsersIcon,
      isActive: pathname?.includes("/organization/members"),
      visible: canViewMembers === true,
    },
    {
      title: "Sessions",
      url: "/organization/sessions",
      icon: LogInIcon,
      isActive: pathname?.includes("/organization/sessions"),
      visible: canViewMembers === true,
    },
    {
      title: "Service accounts",
      url: "/organization/settings/service-accounts",
      icon: KeyRoundIcon,
      isActive: pathname?.includes("/organization/settings/service-accounts"),
      visible: canViewServiceAccounts === true && serviceAccountsEnabled,
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
        {navSettings.some((item) => item.visible === true) && (
          <SidebarGroup>
            <SidebarGroupLabel>Settings</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {navSettings
                  .filter((item) => item.visible === true)
                  .map((item) => (
                    <SidebarMenuItem key={item.title}>
                      <SidebarMenuButton asChild isActive={item.isActive}>
                        <Link href={item.url}>
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                      {item.locked ? (
                        <SidebarMenuBadge>
                          <LockIcon aria-hidden="true" className="size-3.5" />
                          <span className="sr-only">Requires upgrade</span>
                        </SidebarMenuBadge>
                      ) : null}
                    </SidebarMenuItem>
                  ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        {navSecrets.some((item) => item.visible === true) && (
          <SidebarGroup>
            <SidebarGroupLabel>Secrets</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {navSecrets
                  .filter((item) => item.visible === true)
                  .map((item) => (
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
        )}

        {navManage.some((item) => item.visible === true) && (
          <SidebarGroup>
            <SidebarGroupLabel>Manage</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {navManage
                  .filter((item) => item.visible === true)
                  .map((item) => (
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
        )}
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
