"use client"

import {
  BlocksIcon,
  BotIcon,
  BoxIcon,
  ChevronDown,
  ClipboardListIcon,
  GitBranchIcon,
  KeyRound,
  LayersIcon,
  ListChecksIcon,
  ListVideoIcon,
  LockIcon,
  type LucideIcon,
  MousePointerClickIcon,
  NetworkIcon,
  Pyramid,
  SendIcon,
  Sparkles,
  Table2Icon,
  TerminalIcon,
  UsersIcon,
  VariableIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type * as React from "react"
import { useMemo, useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  LockedFeatureChip,
  LockedFeatureModal,
} from "@/components/locked-feature-modal"
import { AppMenu } from "@/components/sidebar/app-menu"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { usePendingApprovalsCount } from "@/hooks/use-pending-approvals-count"
import { formatPendingApprovalCount } from "@/lib/approvals"
import { useWorkspaceId } from "@/providers/workspace-id"

function SidebarHeaderContent({ workspaceId }: { workspaceId: string }) {
  return <AppMenu workspaceId={workspaceId} />
}

type NavItem = {
  title: string
  url?: string
  icon: LucideIcon
  isActive?: boolean
  isLocked?: boolean
  isPendingEntitlement?: boolean
  onSelect?: () => void
  locked?: boolean
  visible?: boolean
  requiredScope?: string
  badgeCount?: number
  badgeLabel?: string
  /** Small status pill shown after the title, e.g. "Beta". */
  tag?: string
  items?: {
    title: string
    url: string
    isActive?: boolean
  }[]
}

function SidebarNavGroup({
  label,
  items,
}: {
  label: string
  items: NavItem[]
}) {
  const visibleItems = items.filter((item) => item.visible === true)
  if (visibleItems.length === 0) {
    return null
  }
  return (
    <Collapsible defaultOpen className="group/collapsible">
      <SidebarGroup>
        <SidebarGroupLabel asChild>
          <CollapsibleTrigger className="w-full">
            {label}
            <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
          </CollapsibleTrigger>
        </SidebarGroupLabel>
        <CollapsibleContent>
          <SidebarGroupContent>
            <SidebarMenu>
              {visibleItems.map((item) => (
                <SidebarMenuItem key={item.title}>
                  {item.items ? (
                    <SidebarMenuItem>
                      <div className="flex w-full items-center gap-2 overflow-hidden rounded-md py-1.5 px-2 text-left text-[13px] text-sidebar-foreground">
                        <item.icon className="size-4 shrink-0" />
                        <span className="font-medium">{item.title}</span>
                      </div>
                      <SidebarMenuSub>
                        {item.items.map((subItem) => (
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
                  ) : item.isLocked ? (
                    <SidebarMenuButton
                      type="button"
                      isActive={item.isActive}
                      onClick={item.onSelect}
                      className="text-muted-foreground"
                    >
                      <item.icon />
                      <span>{item.title}</span>
                      <LockedFeatureChip className="ml-auto shrink-0" />
                    </SidebarMenuButton>
                  ) : item.isPendingEntitlement ? (
                    <SidebarMenuButton
                      type="button"
                      isActive={item.isActive}
                      disabled
                    >
                      <item.icon />
                      <span>{item.title}</span>
                      {item.tag ? (
                        <span className="ml-auto shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium leading-none text-muted-foreground">
                          {item.tag}
                        </span>
                      ) : null}
                    </SidebarMenuButton>
                  ) : (
                    <SidebarMenuButton asChild isActive={item.isActive}>
                      <Link href={item.url!}>
                        <item.icon />
                        <span>{item.title}</span>
                        {item.tag ? (
                          <span className="ml-auto shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium leading-none text-muted-foreground">
                            {item.tag}
                          </span>
                        ) : null}
                      </Link>
                    </SidebarMenuButton>
                  )}
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
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  )
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const workspaceId = useWorkspaceId()
  const basePath = `/workspaces/${workspaceId}`
  const [lockedFeatureDialogOpen, setLockedFeatureDialogOpen] = useState(false)

  // Scope checks for sidebar items
  const canViewWorkflows = useScopeCheck("workflow:read")
  const canViewAgents = useScopeCheck("agent:read")
  const canExecuteAgents = useScopeCheck("agent:execute")
  const canViewTables = useScopeCheck("table:read")
  const canViewVariables = useScopeCheck("variable:read")
  const canViewSecrets = useScopeCheck("secret:read")
  const canViewIntegrations = useScopeCheck("integration:read")
  const canViewActions = useScopeCheck("org:registry:read")
  const canViewInbox = useScopeCheck("inbox:read")
  const canViewMembers = useScopeCheck("workspace:member:read")
  const canViewServiceAccounts = useScopeCheck("workspace:service_account:read")
  const canViewMcpAccess = useScopeCheck("workspace:read")
  const canViewCases = useScopeCheck("case:read")
  // The builder pages need one of these on top of `form:read`; a requester
  // who can only fill in forms has no use for the list.
  const canReadForms = useScopeCheck("form:read")
  const canBuildForms = useScopeCheck(undefined, [
    "form:update",
    "form:submission:read",
  ])
  // Hidden with the feature flag off. Without the entitlement the link stays
  // and the page explains why Forms is unavailable.
  const formsAccess = useFormsAccess()
  const formsVisible = formsAccess === "enabled" || formsAccess === "locked"
  const canViewTunnels = useScopeCheck("tunnel:read")
  // Sync users push/pull; workspace admins manage the repository connection.
  const canOpenGitSync = useScopeCheck(undefined, [
    "workspace_sync:sync",
    "workspace:update",
  ])
  const canAccessMissionControl =
    canExecuteAgents === true && canViewAgents === true
  const shouldLoadEntitlements =
    canViewAgents === true ||
    canExecuteAgents === true ||
    canViewServiceAccounts === true ||
    canViewInbox === true ||
    canOpenGitSync === true
  const {
    hasEntitlement,
    hasEntitlementData,
    isLoading: entitlementsIsLoading,
  } = useEntitlements({
    enabled: shouldLoadEntitlements,
  })
  const entitlementsKnown = !entitlementsIsLoading && hasEntitlementData
  const workspaceChatEnabled = hasEntitlement("workspace_chat")
  const serviceAccountsEnabled = hasEntitlement("service_accounts")
  const gitSyncEnabled = hasEntitlement("git_sync")
  const { data: pendingApprovalsCount = 0 } = usePendingApprovalsCount(
    workspaceId,
    {
      enabled: canViewInbox === true,
    }
  )

  const navWorkspace: NavItem[] = useMemo(
    () => [
      {
        title: "Chat",
        url: `${basePath}/chat`,
        icon: SendIcon,
        isActive: pathname?.startsWith(`${basePath}/chat`),
        visible: canAccessMissionControl,
        isLocked: entitlementsKnown && !workspaceChatEnabled,
        isPendingEntitlement: !entitlementsKnown,
        onSelect:
          entitlementsKnown && !workspaceChatEnabled
            ? () => setLockedFeatureDialogOpen(true)
            : undefined,
      },
      {
        title: "Workflows",
        url: `${basePath}/workflows`,
        icon: WorkflowIcon,
        isActive: pathname?.startsWith(`${basePath}/workflows`),
        visible: canViewWorkflows === true,
      },
      {
        title: "Forms",
        url: `${basePath}/forms`,
        icon: ClipboardListIcon,
        isActive: pathname?.startsWith(`${basePath}/forms`),
        visible:
          formsVisible && canReadForms === true && canBuildForms === true,
      },
      {
        title: "Cases",
        url: `${basePath}/cases`,
        icon: LayersIcon,
        isActive: pathname?.startsWith(`${basePath}/cases`),
        visible: canViewCases === true,
      },
      {
        title: "Agents",
        url: `${basePath}/agents`,
        icon: MousePointerClickIcon,
        isActive: pathname?.startsWith(`${basePath}/agents`),
        visible: canViewAgents === true,
      },
      {
        title: "Skills",
        url: `${basePath}/skills`,
        icon: Pyramid,
        isActive: pathname?.startsWith(`${basePath}/skills`),
        visible: canViewAgents === true,
      },
    ],
    [
      basePath,
      pathname,
      canViewWorkflows,
      formsVisible,
      canReadForms,
      canBuildForms,
      canViewCases,
      canAccessMissionControl,
      entitlementsKnown,
      workspaceChatEnabled,
      canViewAgents,
    ]
  )

  const navConfigure: NavItem[] = useMemo(
    () => [
      {
        title: "Tables",
        url: `${basePath}/tables`,
        icon: Table2Icon,
        isActive: pathname?.startsWith(`${basePath}/tables`),
        visible: canViewTables === true,
      },
      {
        title: "Variables",
        url: `${basePath}/variables`,
        icon: VariableIcon,
        isActive: pathname?.startsWith(`${basePath}/variables`),
        visible: canViewVariables === true,
      },
      {
        title: "Credentials",
        url: `${basePath}/credentials`,
        icon: KeyRound,
        isActive: pathname?.startsWith(`${basePath}/credentials`),
        visible: canViewSecrets === true,
      },
      {
        title: "Integrations",
        url: `${basePath}/integrations`,
        icon: BlocksIcon,
        isActive: pathname?.startsWith(`${basePath}/integrations`),
        visible: canViewIntegrations === true,
      },
      {
        title: "MCP servers",
        url: `${basePath}/mcp-servers`,
        icon: Sparkles,
        isActive: pathname?.startsWith(`${basePath}/mcp-servers`),
        visible: canViewIntegrations === true,
      },
      {
        title: "Actions",
        url: `${basePath}/actions`,
        icon: BoxIcon,
        isActive: pathname?.startsWith(`${basePath}/actions`),
        visible: canViewActions === true,
      },
    ],
    [
      basePath,
      pathname,
      canViewTables,
      canViewVariables,
      canViewSecrets,
      canViewIntegrations,
      canViewActions,
    ]
  )

  const navMonitor: NavItem[] = [
    {
      title: "Runs",
      url: `${basePath}/runs`,
      icon: ListVideoIcon,
      isActive: pathname?.startsWith(`${basePath}/runs`),
      visible: canViewWorkflows === true,
    },
    {
      title: "Inbox",
      url: `${basePath}/inbox`,
      icon: ListChecksIcon,
      isActive: pathname?.startsWith(`${basePath}/inbox`),
      visible: canViewInbox === true,
      badgeCount: pendingApprovalsCount,
      badgeLabel: `${pendingApprovalsCount} pending approval${pendingApprovalsCount === 1 ? "" : "s"}`,
    },
  ]

  return (
    <Sidebar collapsible="offcanvas" variant="inset" {...props}>
      <SidebarHeader>
        <SidebarHeaderContent workspaceId={workspaceId} />
      </SidebarHeader>
      <SidebarContent>
        <LockedFeatureModal
          open={lockedFeatureDialogOpen}
          onOpenChange={setLockedFeatureDialogOpen}
        />
        <SidebarNavGroup label="Workspace" items={navWorkspace} />
        <SidebarNavGroup label="Configure" items={navConfigure} />
        {navMonitor.some((item) => item.visible === true) && (
          <Collapsible defaultOpen className="group/collapsible">
            <SidebarGroup>
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger className="w-full">
                  Monitor
                  <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <CollapsibleContent>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {navMonitor
                      .filter((item) => item.visible === true)
                      .map((item) => (
                        <SidebarMenuItem key={item.title}>
                          <SidebarMenuButton
                            asChild
                            isActive={item.isActive}
                            className={item.badgeCount ? "pr-9" : undefined}
                          >
                            <Link href={item.url!}>
                              <item.icon />
                              <span>{item.title}</span>
                            </Link>
                          </SidebarMenuButton>
                          {item.badgeCount ? (
                            <SidebarMenuBadge
                              aria-label={item.badgeLabel}
                              className="bg-violet-500/10 text-violet-700 dark:text-violet-300"
                            >
                              {formatPendingApprovalCount(item.badgeCount)}
                            </SidebarMenuBadge>
                          ) : null}
                        </SidebarMenuItem>
                      ))}
                  </SidebarMenu>
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        )}
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav
          showManageLabel={false}
          manageItems={[
            canViewMembers === true
              ? {
                  title: "Members",
                  href: `${basePath}/members`,
                  icon: UsersIcon,
                  isActive: pathname?.startsWith(`${basePath}/members`),
                }
              : null,
            canViewServiceAccounts === true && serviceAccountsEnabled
              ? {
                  title: "Service accounts",
                  href: `${basePath}/service-accounts`,
                  icon: BotIcon,
                  isActive: pathname?.startsWith(
                    `${basePath}/service-accounts`
                  ),
                }
              : null,
            canOpenGitSync === true
              ? {
                  title: "Git sync",
                  href: `${basePath}/git-sync`,
                  icon: GitBranchIcon,
                  isActive: pathname?.startsWith(`${basePath}/git-sync`),
                  // Stays visible without the entitlement so the page can offer the upgrade.
                  locked: entitlementsKnown && !gitSyncEnabled,
                }
              : null,
            canViewTunnels === true
              ? {
                  title: "Tunnels",
                  href: `${basePath}/tunnels`,
                  icon: NetworkIcon,
                  isActive: pathname?.startsWith(`${basePath}/tunnels`),
                }
              : null,
            canViewMcpAccess === true
              ? {
                  title: "MCP access",
                  href: `${basePath}/mcp`,
                  icon: TerminalIcon,
                  isActive:
                    pathname === `${basePath}/mcp` ||
                    pathname?.startsWith(`${basePath}/mcp/`),
                }
              : null,
          ].filter((item) => item !== null)}
        />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
