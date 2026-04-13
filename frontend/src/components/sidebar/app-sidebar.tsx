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
  MousePointerClickIcon,
  Table2Icon,
  UsersIcon,
  VariableIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import type * as React from "react"
import { useEffect, useRef, useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { LockedFeatureModal } from "@/components/locked-feature-modal"
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
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { useWorkspaceId } from "@/providers/workspace-id"

function SidebarHeaderContent({ workspaceId }: { workspaceId: string }) {
  return <AppMenu workspaceId={workspaceId} />
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const workspaceId = useWorkspaceId()
  const params = useParams<{ caseId?: string }>()
  const { setOpen: setSidebarOpen } = useSidebar()
  const setSidebarOpenRef = useRef(setSidebarOpen)
  const basePath = `/workspaces/${workspaceId}`
  const caseId = params?.caseId
  const casesListPath = `${basePath}/cases`
  const isCasesList = pathname === casesListPath
  const [lockedFeatureDialogOpen, setLockedFeatureDialogOpen] = useState(false)

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

  type NavItem = {
    title: string
    url?: string
    icon: LucideIcon
    isActive?: boolean
    visible?: boolean
    requiredScope?: string
    items?: {
      title: string
      url: string
      isActive?: boolean
    }[]
  }

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

  const navWorkspace: NavItem[] = [
    {
      title: "Workflows",
      url: `${basePath}/workflows`,
      icon: WorkflowIcon,
      isActive: pathname?.startsWith(`${basePath}/workflows`),
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
      title: "Agents",
      url: `${basePath}/agents`,
      icon: MousePointerClickIcon,
      isActive: pathname?.startsWith(`${basePath}/agents`),
      visible: canViewAgents === true,
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
      title: "Actions",
      url: `${basePath}/actions`,
      icon: BoxIcon,
      isActive: pathname?.startsWith(`${basePath}/actions`),
      visible: canViewActions === true,
    },
  ]

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
