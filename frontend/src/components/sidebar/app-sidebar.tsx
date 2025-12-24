"use client"

import {
  BotIcon,
  BracketsIcon,
  ChevronDown,
  KeyRoundIcon,
  type LucideIcon,
  SquarePlus,
  SquareStackIcon,
  Table2Icon,
  UserCheckIcon,
  UsersIcon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"
import Link from "next/link"
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation"
import type * as React from "react"
import { useCallback, useEffect, useRef, useState } from "react"
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
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { useCreateChat, useListChats } from "@/hooks/use-chat"
import { useWorkspaceId } from "@/providers/workspace-id"

function SidebarHeaderContent({ workspaceId }: { workspaceId: string }) {
  return <AppMenu workspaceId={workspaceId} />
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const params = useParams<{ caseId?: string }>()
  const { setOpen: setSidebarOpen } = useSidebar()
  const setSidebarOpenRef = useRef(setSidebarOpen)
  const basePath = `/workspaces/${workspaceId}`
  const caseId = params?.caseId
  const casesListPath = `${basePath}/cases`
  const isCasesList = pathname === casesListPath
  const [isCreatingChat, setIsCreatingChat] = useState(false)
  const { createChat } = useCreateChat(workspaceId)

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
      title: "New chat",
      url: `${basePath}/copilot`,
      icon: SquarePlus,
      isActive:
        pathname === `${basePath}/copilot` && !searchParams?.get("chatId"),
    },
    {
      title: "Approvals",
      url: `${basePath}/approvals`,
      icon: UserCheckIcon,
      isActive: pathname?.startsWith(`${basePath}/approvals`),
    },
  ]

  const navWorkspace = [
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
      title: "Agents",
      url: `${basePath}/agents`,
      icon: BotIcon,
      isActive: pathname?.startsWith(`${basePath}/agents`),
    },
    {
      title: "Tables",
      url: `${basePath}/tables`,
      icon: Table2Icon,
      isActive: pathname?.startsWith(`${basePath}/tables`),
    },
    {
      title: "Variables",
      url: `${basePath}/variables`,
      icon: BracketsIcon,
      isActive: pathname?.startsWith(`${basePath}/variables`),
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

  const { chats } = useListChats({
    workspaceId,
    entityType: "copilot",
    entityId: workspaceId,
    limit: 100,
  })

  const recentChats = chats?.slice(0, 10)

  const handleNewChat = useCallback(async () => {
    if (isCreatingChat) return
    setIsCreatingChat(true)
    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: "copilot",
        entity_id: workspaceId,
      })
      router.push(`${basePath}/copilot?chatId=${newChat.id}`)
    } catch (error) {
      console.error("Failed to create chat:", error)
    } finally {
      setIsCreatingChat(false)
    }
  }, [isCreatingChat, createChat, workspaceId, router, basePath, chats])

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
                    {item.title === "New chat" ? (
                      <SidebarMenuButton
                        onClick={handleNewChat}
                        disabled={isCreatingChat}
                        isActive={item.isActive}
                      >
                        <item.icon />
                        <span>{item.title}</span>
                      </SidebarMenuButton>
                    ) : (
                      <SidebarMenuButton asChild isActive={item.isActive}>
                        <Link href={item.url}>
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                    )}
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
        <Collapsible defaultOpen className="group/collapsible">
          <SidebarGroup>
            <SidebarGroupLabel asChild>
              <CollapsibleTrigger>
                Chats
                <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
              </CollapsibleTrigger>
            </SidebarGroupLabel>
            <CollapsibleContent>
              <SidebarGroupContent>
                <SidebarMenu>
                  {recentChats?.map((chat) => (
                    <SidebarMenuItem key={chat.id}>
                      <SidebarMenuButton
                        asChild
                        isActive={
                          pathname === `${basePath}/copilot` &&
                          searchParams?.get("chatId") === chat.id
                        }
                      >
                        <Link href={`${basePath}/copilot?chatId=${chat.id}`}>
                          <span className="truncate">
                            {chat.title || "Untitled Chat"}
                          </span>
                        </Link>
                      </SidebarMenuButton>
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
