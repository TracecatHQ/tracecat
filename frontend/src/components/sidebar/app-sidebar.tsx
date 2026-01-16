"use client"

import {
  BlocksIcon,
  ChevronDown,
  InboxIcon,
  LockKeyholeIcon,
  type LucideIcon,
  MessageSquare,
  SquareMousePointerIcon,
  SquareStackIcon,
  Table2Icon,
  UsersIcon,
  VariableIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import type * as React from "react"
import { useEffect, useRef } from "react"
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
import { useListChats } from "@/hooks/use-chat"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
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
  const { isFeatureEnabled } = useFeatureFlag()
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const { chats } = useListChats({
    workspaceId,
    entityType: "copilot",
    entityId: workspaceId,
    limit: 1,
  })
  const mostRecentChatId = chats?.[0]?.id
  const copilotUrl = mostRecentChatId
    ? `${basePath}/copilot?chatId=${mostRecentChatId}`
    : `${basePath}/copilot`

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
    items?: {
      title: string
      url: string
      isActive?: boolean
    }[]
  }

  const navMain: NavItem[] = [
    {
      title: "Chats",
      url: copilotUrl,
      icon: MessageSquare,
      isActive: pathname === `${basePath}/copilot`,
    },
    {
      title: "Inbox",
      url: `${basePath}/inbox`,
      icon: InboxIcon,
      isActive: pathname?.startsWith(`${basePath}/inbox`),
    },
  ]

  const navWorkspace: NavItem[] = [
    {
      title: "Workflows",
      url: `${basePath}/workflows`,
      icon: WorkflowIcon,
      isActive: pathname?.startsWith(`${basePath}/workflows`),
    },
    {
      title: "Cases",
      url: `${basePath}/cases`,
      icon: SquareStackIcon,
      isActive: pathname?.startsWith(`${basePath}/cases`),
    },
    ...(agentPresetsEnabled
      ? [
          {
            title: "Agents",
            url: `${basePath}/agents`,
            icon: SquareMousePointerIcon,
            isActive: pathname?.startsWith(`${basePath}/agents`),
          },
        ]
      : []),
    {
      title: "Tables",
      url: `${basePath}/tables`,
      icon: Table2Icon,
      isActive: pathname?.startsWith(`${basePath}/tables`),
    },
    {
      title: "Variables",
      url: `${basePath}/variables`,
      icon: VariableIcon,
      isActive: pathname?.startsWith(`${basePath}/variables`),
    },
    {
      title: "Credentials",
      url: `${basePath}/credentials`,
      icon: LockKeyholeIcon,
      isActive: pathname?.startsWith(`${basePath}/credentials`),
    },
    {
      title: "Integrations",
      url: `${basePath}/integrations`,
      icon: BlocksIcon,
      isActive: pathname?.startsWith(`${basePath}/integrations`),
    },
    {
      title: "Members",
      url: `${basePath}/members`,
      icon: UsersIcon,
      isActive: pathname?.startsWith(`${basePath}/members`),
    },
  ]

  return (
    <Sidebar collapsible="offcanvas" variant="inset" {...props}>
      <SidebarHeader>
        <SidebarHeaderContent workspaceId={workspaceId} />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navMain
                .filter((item) => item.visible !== false)
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
        </SidebarGroup>
        <Collapsible defaultOpen className="group/collapsible">
          <SidebarGroup>
            <SidebarGroupLabel asChild>
              <CollapsibleTrigger>
                Workspace
                <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
              </CollapsibleTrigger>
            </SidebarGroupLabel>
            <CollapsibleContent>
              <SidebarGroupContent>
                <SidebarMenu>
                  {navWorkspace
                    .filter((item) => item.visible !== false)
                    .map((item) => (
                      <SidebarMenuItem key={item.title}>
                        {item.items ? (
                          <SidebarMenuItem>
                            <div className="flex w-full items-center gap-2 overflow-hidden rounded-md py-1.5 px-2 text-left text-[13px] text-zinc-700 dark:text-zinc-300">
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
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
