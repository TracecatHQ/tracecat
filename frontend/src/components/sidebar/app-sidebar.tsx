"use client"

import {
  BlocksIcon,
  BoxIcon,
  ChevronDown,
  KeyRound,
  LayersIcon,
  ListChecksIcon,
  ListVideoIcon,
  type LucideIcon,
  Plus,
  Pyramid,
  Table2Icon,
  UsersIcon,
  VariableIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useParams, usePathname, useRouter } from "next/navigation"
import type * as React from "react"
import { useEffect, useMemo, useRef, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  LockedFeatureChip,
  LockedFeatureModal,
} from "@/components/locked-feature-modal"
import { AppMenu } from "@/components/sidebar/app-menu"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import { Button } from "@/components/ui/button"
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
  SidebarGroupAction,
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
  useSidebar,
} from "@/components/ui/sidebar"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"
import { shortTimeAgo } from "@/lib/utils"
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
  onSelect?: () => void
  visible?: boolean
  requiredScope?: string
  items?: {
    title: string
    url: string
    isActive?: boolean
  }[]
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const params = useParams<{ caseId?: string }>()
  const { setOpen: setSidebarOpen } = useSidebar()
  const setSidebarOpenRef = useRef(setSidebarOpen)
  const basePath = `/workspaces/${workspaceId}`
  const caseId = params?.caseId
  const casesListPath = `${basePath}/cases`
  const isCasesList = pathname === casesListPath
  const isAgentsRoute = pathname?.startsWith(`${basePath}/agents`) ?? false
  const [lockedFeatureDialogOpen, setLockedFeatureDialogOpen] = useState(false)
  const [agentsSectionOpen, setAgentsSectionOpen] = useState(isAgentsRoute)

  useEffect(() => {
    setSidebarOpenRef.current = setSidebarOpen
  }, [setSidebarOpen])

  useEffect(() => {
    const updateSidebarOpen = setSidebarOpenRef.current
    if (caseId) {
      updateSidebarOpen(false)
    } else if (isCasesList) {
      updateSidebarOpen(true)
    }
  }, [caseId, isCasesList])

  // Scope checks for sidebar items
  const canViewWorkflows = useScopeCheck("workflow:read")
  const canViewAgents = useScopeCheck("agent:read")
  const canViewTables = useScopeCheck("table:read")
  const canViewVariables = useScopeCheck("variable:read")
  const canViewSecrets = useScopeCheck("secret:read")
  const canViewIntegrations = useScopeCheck("integration:read")
  const canViewActions = useScopeCheck("org:registry:read")
  const canViewInbox = useScopeCheck("inbox:read")
  const canViewMembers = useScopeCheck("workspace:member:read")
  const canViewCases = useScopeCheck("case:read")
<<<<<<< HEAD
||||||| parent of de24ee634 (Improve skills studio and local MCP uploads)
  const canCreateCase = useScopeCheck("case:create")
=======
  const canCreateCase = useScopeCheck("case:create")
  const shouldLoadAgentEntitlements = canViewAgents === true
>>>>>>> de24ee634 (Improve skills studio and local MCP uploads)
  const shouldLoadAgentsSection =
    shouldLoadAgentEntitlements && (agentsSectionOpen || isAgentsRoute)
  const { hasEntitlement, isLoading: entitlementsIsLoading } = useEntitlements({
    enabled: shouldLoadAgentEntitlements,
  })
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const { presets, presetsIsLoading } = useAgentPresets(workspaceId, {
    enabled: shouldLoadAgentsSection && agentAddonsEnabled,
  })

  useEffect(() => {
    if (isAgentsRoute) {
      setAgentsSectionOpen(true)
    }
  }, [isAgentsRoute])

  const openNewAgentBuilder = () => {
    router.push(`${basePath}/agents/new`)
  }

  const navWorkspace: NavItem[] = useMemo(
    () => [
      {
        title: "Workflows",
        url: `${basePath}/workflows`,
        icon: WorkflowIcon,
        isActive: pathname?.startsWith(`${basePath}/workflows`),
        visible: canViewWorkflows === true,
      },
      {
        title: "Runs",
        url: `${basePath}/runs`,
        icon: ListVideoIcon,
        isActive: pathname?.startsWith(`${basePath}/runs`),
        visible: canViewWorkflows === true,
      },
      {
        title: "Cases",
        url: `${basePath}/cases`,
        icon: LayersIcon,
        isActive: pathname?.startsWith(`${basePath}/cases`),
        visible: canViewCases === true,
      },
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
        title: "Skills",
        url: `${basePath}/skills`,
        icon: Pyramid,
        isActive: pathname?.startsWith(`${basePath}/skills`),
        isLocked: entitlementsIsLoading || !agentAddonsEnabled,
        onSelect: entitlementsIsLoading
          ? undefined
          : () => setLockedFeatureDialogOpen(true),
        visible: canViewAgents === true,
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
      canViewWorkflows,
      canViewCases,
      canViewTables,
      canViewVariables,
      canViewSecrets,
      canViewIntegrations,
      entitlementsIsLoading,
      agentAddonsEnabled,
      canViewAgents,
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
      title: "Approvals",
      url: `${basePath}/inbox`,
      icon: ListChecksIcon,
      isActive: pathname?.startsWith(`${basePath}/inbox`),
      visible: canViewInbox === true,
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
        {navWorkspace.some((item) => item.visible === true) && (
          <Collapsible defaultOpen className="group/collapsible">
            <SidebarGroup>
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger className="w-full">
                  Workspace
                  <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <CollapsibleContent>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {navWorkspace
                      .filter((item) => item.visible === true)
                      .map((item) => (
                        <SidebarMenuItem key={item.title}>
                          {item.items ? (
                            <SidebarMenuItem>
                              <div className="flex w-full items-center gap-2 overflow-hidden rounded-md py-1.5 px-2 text-left text-[13px] text-zinc-700 dark:text-zinc-300">
                                <item.icon className="size-4 shrink-0" />
                                <span className="font-medium">
                                  {item.title}
                                </span>
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
                          ) : (
                            <SidebarMenuButton asChild isActive={item.isActive}>
                              <Link href={item.url!}>
                                <item.icon />
                                <span>{item.title}</span>
                              </Link>
                            </SidebarMenuButton>
                          )}
                        </SidebarMenuItem>
                      ))}
                  </SidebarMenu>
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        )}
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
                          <SidebarMenuButton asChild isActive={item.isActive}>
                            <Link href={item.url!}>
                              <item.icon />
                              <span>{item.title}</span>
                            </Link>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      ))}
                  </SidebarMenu>
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        )}

        {canViewAgents === true && (
          <Collapsible
            open={agentsSectionOpen}
            onOpenChange={setAgentsSectionOpen}
            className="group/collapsible"
          >
            <SidebarGroup className="group/agents relative">
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger className="w-full">
                  Agents
                  <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              {agentAddonsEnabled ? (
                <SidebarGroupAction
                  aria-label="Create agent"
                  onClick={openNewAgentBuilder}
                  className={[
                    "right-10 opacity-0 pointer-events-none transition-opacity",
                    "group-hover/agents:opacity-100 group-hover/agents:pointer-events-auto",
                    "group-focus-within/agents:opacity-100 group-focus-within/agents:pointer-events-auto",
                  ].join(" ")}
                >
                  <Plus />
                </SidebarGroupAction>
              ) : null}
              <CollapsibleContent>
                <SidebarGroupContent>
                  {entitlementsIsLoading ? (
                    <div className="px-2 py-3 text-xs text-muted-foreground">
                      Loading agents…
                    </div>
                  ) : agentAddonsEnabled ? (
                    presetsIsLoading ? (
                      <div className="px-2 py-3 text-xs text-muted-foreground">
                        Loading agents…
                      </div>
                    ) : presets && presets.length > 0 ? (
                      <SidebarMenu>
                        {presets.map((preset) => (
                          <AgentPresetSidebarItem
                            key={preset.id}
                            preset={preset}
                            isActive={
                              pathname === `${basePath}/agents/${preset.id}`
                            }
                            href={`${basePath}/agents/${preset.id}`}
                          />
                        ))}
                      </SidebarMenu>
                    ) : (
                      <Button
                        variant="link"
                        className="h-auto px-2 py-1 text-xs"
                        onClick={openNewAgentBuilder}
                      >
                        Create first agent
                      </Button>
                    )
                  ) : (
                    <SidebarMenu>
                      <SidebarMenuItem>
                        <SidebarMenuButton
                          type="button"
                          onClick={() => setLockedFeatureDialogOpen(true)}
                          className="h-auto py-2 text-muted-foreground"
                        >
                          <LayersIcon />
                          <span>Case Agent</span>
                          <LockedFeatureChip className="ml-auto shrink-0" />
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                      <SidebarMenuItem>
                        <SidebarMenuButton
                          type="button"
                          onClick={() => setLockedFeatureDialogOpen(true)}
                          className="h-auto py-2 text-muted-foreground"
                        >
                          <Table2Icon />
                          <span>Table Agent</span>
                          <LockedFeatureChip className="ml-auto shrink-0" />
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    </SidebarMenu>
                  )}
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        )}
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav
          settingsItems={
            canViewMembers === true
              ? [
                  {
                    title: "Members",
                    href: `${basePath}/members`,
                    icon: UsersIcon,
                    isActive: pathname?.startsWith(`${basePath}/members`),
                  },
                ]
              : undefined
          }
        />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function AgentPresetSidebarItem({
  preset,
  isActive,
  href,
}: {
  preset: AgentPresetReadMinimal
  isActive: boolean
  href: string
}) {
  const shortUpdatedAtRaw = shortTimeAgo(new Date(preset.updated_at))
  const shortUpdatedAt =
    shortUpdatedAtRaw === "just now"
      ? "now"
      : shortUpdatedAtRaw.replace(" ago", "")

  return (
    <SidebarMenuItem>
      <SidebarMenuButton asChild isActive={isActive} className="h-auto py-2">
        <Link href={href} className="flex w-full min-w-0 items-center gap-2">
          <p className="truncate text-xs font-normal">{preset.name}</p>
          <p className="ml-auto shrink-0 text-xs text-muted-foreground">
            {shortUpdatedAt}
          </p>
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}
