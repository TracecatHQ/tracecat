"use client"

import {
  BoxIcon,
  KeyRoundIcon,
  ListTodoIcon,
  type LucideIcon,
  ShapesIcon,
  SquareStackIcon,
  Table2Icon,
  UsersIcon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"
import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import type * as React from "react"
import { useEffect, useRef } from "react"
import { AppMenu } from "@/components/sidebar/app-menu"
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
  useSidebar,
} from "@/components/ui/sidebar"
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
  const { isFeatureEnabled } = useFeatureFlag()
  const basePath = `/workspaces/${workspaceId}`
  const caseId = params?.caseId
  const casesListPath = `${basePath}/cases`
  const isCasesList = pathname === casesListPath

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
    url: string
    icon: LucideIcon
    isActive?: boolean
    visible?: boolean
  }

  const navMain: NavItem[] = [
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
    {
      title: "Runbooks",
      url: `${basePath}/runbooks`,
      icon: ListTodoIcon,
      isActive: pathname?.startsWith(`${basePath}/runbooks`),
      visible: isFeatureEnabled("runbooks"),
    },
  ]

  const navWorkspace = [
    {
      title: "Tables",
      url: `${basePath}/tables`,
      icon: Table2Icon,
      isActive: pathname?.startsWith(`${basePath}/tables`),
    },
    {
      title: "Records",
      url: `${basePath}/records`,
      icon: BoxIcon,
      isActive: pathname?.startsWith(`${basePath}/records`),
    },
    {
      title: "Entities",
      url: `${basePath}/entities`,
      icon: ShapesIcon,
      isActive: pathname?.startsWith(`${basePath}/entities`),
    },
    {
      title: "Credentials",
      url: `${basePath}/credentials`,
      icon: KeyRoundIcon,
      isActive: pathname?.startsWith(`${basePath}/credentials`),
    },
    {
      title: "Integrations",
      url: `${basePath}/integrations`,
      icon: ZapIcon,
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
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navWorkspace.map((item) => (
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
