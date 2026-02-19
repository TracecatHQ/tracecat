"use client"

import { useQuery } from "@tanstack/react-query"
import {
  BlocksIcon,
  ChevronDown,
  InboxIcon,
  KeyRound,
  LayersIcon,
  LayersPlus,
  type LucideIcon,
  SquareMousePointerIcon,
  SquarePen,
  Table2Icon,
  Trash2Icon,
  UsersIcon,
  VariableIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import {
  useParams,
  usePathname,
  useRouter,
  useSearchParams,
} from "next/navigation"
import type * as React from "react"
import { useEffect, useMemo, useRef, useState } from "react"
import { casesGetCase } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CreateCaseDialog } from "@/components/cases/case-create-dialog"
import { AppMenu } from "@/components/sidebar/app-menu"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { parseChatError, useDeleteChat, useListChats } from "@/hooks/use-chat"
import { useEntitlements } from "@/hooks/use-entitlements"
import { cn, shortTimeAgo } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

function SidebarHeaderContent({ workspaceId }: { workspaceId: string }) {
  return <AppMenu workspaceId={workspaceId} />
}

function formatChatLastActive(updatedAt: string): string {
  const date = new Date(updatedAt)
  if (Number.isNaN(date.getTime())) {
    return ""
  }

  const shortTime = shortTimeAgo(date)
  if (shortTime === "just now") {
    return "now"
  }
  return shortTime.replace(/\s+ago$/, "")
}

function isSessionEntityType(
  entityType: string
): entityType is "copilot" | "case" {
  return entityType === "copilot" || entityType === "case"
}

type CaseSidebarInfo = {
  shortId: string
  summary: string
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
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const [createCaseDialogOpen, setCreateCaseDialogOpen] = useState(false)
  const [confirmDeleteChatId, setConfirmDeleteChatId] = useState<string | null>(
    null
  )
  const canExecuteAgents = useScopeCheck("agent:execute")
  const { deleteChat, isDeleting: deleteChatPending } =
    useDeleteChat(workspaceId)
  const {
    chats: sidebarChats,
    chatsLoading: sidebarChatsLoading,
    chatsError: sidebarChatsError,
  } = useListChats(
    {
      workspaceId,
      limit: 100,
    },
    { enabled: canExecuteAgents === true }
  )
  const selectedSessionId = searchParams?.get("chatId")
  const isCopilotPage = pathname?.startsWith(`${basePath}/copilot`)
  const isCasePage = pathname?.startsWith(`${basePath}/cases/`)
  const recentSessions = useMemo(
    () =>
      [...(sidebarChats ?? [])]
        .filter((chat) => isSessionEntityType(chat.entity_type))
        .sort(
          (a, b) =>
            new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        )
        .slice(0, 20),
    [sidebarChats]
  )
  const firstCopilotSessionId = useMemo(
    () => recentSessions.find((chat) => chat.entity_type === "copilot")?.id,
    [recentSessions]
  )
  const firstCaseSessionIdByCaseId = useMemo(() => {
    const map = new Map<string, string>()
    for (const chat of recentSessions) {
      if (chat.entity_type === "case" && !map.has(chat.entity_id)) {
        map.set(chat.entity_id, chat.id)
      }
    }
    return map
  }, [recentSessions])
  const caseEntityIdsForSessions = useMemo(
    () =>
      Array.from(
        new Set(
          recentSessions
            .filter((chat) => chat.entity_type === "case")
            .map((chat) => chat.entity_id)
        )
      ),
    [recentSessions]
  )
  const { data: caseInfoByEntityId = {} } = useQuery<
    Record<string, CaseSidebarInfo>
  >({
    queryKey: ["sidebar-case-info", workspaceId, caseEntityIdsForSessions],
    queryFn: async () => {
      const entries = await Promise.all(
        caseEntityIdsForSessions.map(async (entityId) => {
          try {
            const caseData = await casesGetCase({
              caseId: entityId,
              workspaceId,
            })
            return [
              entityId,
              {
                shortId: caseData.short_id,
                summary: caseData.summary,
              },
            ] as const
          } catch (error) {
            console.warn(`Failed to load case ${entityId} for sidebar`, error)
            return [entityId, null] as const
          }
        })
      )

      return entries.reduce<Record<string, CaseSidebarInfo>>(
        (acc, [entityId, info]) => {
          if (info) {
            acc[entityId] = info
          }
          return acc
        },
        {}
      )
    },
    enabled: canExecuteAgents === true && caseEntityIdsForSessions.length > 0,
  })

  const handleNewChat = () => {
    if (canExecuteAgents !== true) {
      return
    }
    router.push(`${basePath}/copilot`)
  }

  const handleDeleteChat = async (
    chatId: string,
    event: React.MouseEvent<HTMLButtonElement>
  ) => {
    event.preventDefault()
    event.stopPropagation()

    try {
      setConfirmDeleteChatId(null)
      await deleteChat({ chatId })

      if (selectedSessionId === chatId) {
        if (isCopilotPage) {
          router.push(`${basePath}/copilot`)
        } else if (isCasePage && caseId) {
          router.push(`${basePath}/cases/${caseId}`)
        }
      }
    } catch (error) {
      console.error("Failed to delete chat:", error)
      toast({
        variant: "destructive",
        title: "Failed to delete session",
        description: parseChatError(error),
      })
    }
  }

  const handleRequestDeleteChat = (
    chatId: string,
    event: React.MouseEvent<HTMLButtonElement>
  ) => {
    event.preventDefault()
    event.stopPropagation()

    setConfirmDeleteChatId((current) => (current === chatId ? null : chatId))
  }

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
  const canViewInbox = useScopeCheck("inbox:read")
  const canViewMembers = useScopeCheck("workspace:member:read")
  const canViewCases = useScopeCheck("case:read")
  const canCreateCase = useScopeCheck("case:create")

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
    ...(agentAddonsEnabled
      ? [
          {
            title: "Agents",
            url: `${basePath}/agents`,
            icon: SquareMousePointerIcon,
            isActive: pathname?.startsWith(`${basePath}/agents`),
            visible: canViewAgents === true,
          },
        ]
      : []),
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
      title: "Members",
      url: `${basePath}/members`,
      icon: UsersIcon,
      isActive: pathname?.startsWith(`${basePath}/members`),
      visible: canViewMembers === true,
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
              {canExecuteAgents === true && (
                <SidebarMenuItem>
                  <SidebarMenuButton onClick={handleNewChat}>
                    <SquarePen />
                    <span>New chat</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
              {canCreateCase === true && (
                <SidebarMenuItem>
                  <SidebarMenuButton
                    onClick={() => setCreateCaseDialogOpen(true)}
                  >
                    <LayersPlus />
                    <span>Add case</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
              {canViewInbox === true && (
                <SidebarMenuItem>
                  <SidebarMenuButton
                    asChild
                    isActive={pathname?.startsWith(`${basePath}/inbox`)}
                  >
                    <Link href={`${basePath}/inbox`}>
                      <InboxIcon />
                      <span>Inbox</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
            </SidebarMenu>
            <CreateCaseDialog
              open={createCaseDialogOpen}
              onOpenChange={setCreateCaseDialogOpen}
            />
          </SidebarGroupContent>
        </SidebarGroup>
        {canExecuteAgents === true && (
          <Collapsible defaultOpen className="group/collapsible">
            <SidebarGroup>
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger>
                  Sessions
                  <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <CollapsibleContent>
                <SidebarGroupContent>
                  <TooltipProvider delayDuration={0}>
                    <div className="h-[7.75rem]">
                      {sidebarChatsLoading ? (
                        <div className="px-2 py-1 text-xs text-muted-foreground">
                          Loading sessions...
                        </div>
                      ) : sidebarChatsError ? (
                        <div className="px-2 py-1 text-xs text-red-600">
                          Failed to load sessions
                        </div>
                      ) : recentSessions.length === 0 ? (
                        <div className="px-2 py-1 text-xs text-muted-foreground">
                          No sessions yet
                        </div>
                      ) : (
                        <ScrollArea className="h-full">
                          <SidebarMenuSub className="mx-0 border-l-0 px-0 pr-1">
                            {recentSessions.map((chat) => {
                              const isCaseSession = chat.entity_type === "case"
                              const caseInfo = isCaseSession
                                ? caseInfoByEntityId[chat.entity_id]
                                : undefined
                              const caseShortId = caseInfo?.shortId
                              const caseSummary = caseInfo?.summary
                              const caseHref = `${basePath}/cases/${chat.entity_id}`
                              const sessionHref = isCaseSession
                                ? `${caseHref}?chatId=${chat.id}`
                                : `${basePath}/copilot?chatId=${chat.id}`
                              const lastActive = formatChatLastActive(
                                chat.updated_at
                              )
                              const isChatActive =
                                chat.entity_type === "copilot"
                                  ? isCopilotPage &&
                                    (selectedSessionId
                                      ? selectedSessionId === chat.id
                                      : firstCopilotSessionId === chat.id)
                                  : isCasePage &&
                                    caseId === chat.entity_id &&
                                    (selectedSessionId
                                      ? selectedSessionId === chat.id
                                      : firstCaseSessionIdByCaseId.get(
                                          caseId
                                        ) === chat.id)
                              const isDeleteConfirming =
                                confirmDeleteChatId === chat.id

                              return (
                                <SidebarMenuSubItem
                                  key={chat.id}
                                  className="group/menu-item relative"
                                >
                                  {isCaseSession &&
                                  caseSummary &&
                                  !isDeleteConfirming ? (
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <SidebarMenuSubButton
                                          asChild
                                          isActive={isChatActive}
                                        >
                                          <Link href={sessionHref}>
                                            <span
                                              className={cn(
                                                "text-xs",
                                                "transition-opacity group-hover/menu-item:opacity-0"
                                              )}
                                            >
                                              {chat.title || "Untitled chat"}
                                            </span>
                                            {lastActive ? (
                                              <time
                                                dateTime={chat.updated_at}
                                                className="ml-auto shrink-0 text-[11px] text-zinc-500 transition-opacity group-hover/menu-item:opacity-0 dark:text-zinc-400"
                                              >
                                                {lastActive}
                                              </time>
                                            ) : null}
                                          </Link>
                                        </SidebarMenuSubButton>
                                      </TooltipTrigger>
                                      <TooltipContent
                                        side="bottom"
                                        align="start"
                                      >
                                        {caseSummary}
                                      </TooltipContent>
                                    </Tooltip>
                                  ) : (
                                    <SidebarMenuSubButton
                                      asChild
                                      isActive={isChatActive}
                                    >
                                      <Link href={sessionHref}>
                                        <span
                                          className={cn(
                                            "text-xs",
                                            isCaseSession &&
                                              !isDeleteConfirming &&
                                              "transition-opacity group-hover/menu-item:opacity-0"
                                          )}
                                        >
                                          {chat.title || "Untitled chat"}
                                        </span>
                                        {lastActive ? (
                                          <time
                                            dateTime={chat.updated_at}
                                            className={cn(
                                              "ml-auto shrink-0 text-[11px] text-zinc-500 transition-opacity dark:text-zinc-400",
                                              isDeleteConfirming
                                                ? "opacity-0"
                                                : "group-hover/menu-item:opacity-0"
                                            )}
                                          >
                                            {lastActive}
                                          </time>
                                        ) : null}
                                      </Link>
                                    </SidebarMenuSubButton>
                                  )}
                                  {isCaseSession && caseShortId ? (
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <Link
                                          href={caseHref}
                                          className={cn(
                                            "absolute left-2 top-1/2 z-10 hidden -translate-y-1/2 items-center rounded-md border border-border/70 bg-background/95 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-foreground transition-colors hover:bg-muted group-data-[collapsible=icon]:hidden",
                                            !isDeleteConfirming &&
                                              "group-hover/menu-item:inline-flex"
                                          )}
                                          aria-label={`Open case ${caseShortId}`}
                                        >
                                          {caseShortId}
                                        </Link>
                                      </TooltipTrigger>
                                      {caseSummary ? (
                                        <TooltipContent
                                          side="bottom"
                                          align="start"
                                        >
                                          {caseSummary}
                                        </TooltipContent>
                                      ) : null}
                                    </Tooltip>
                                  ) : null}
                                  {isDeleteConfirming ? (
                                    <button
                                      type="button"
                                      onClick={(event) =>
                                        void handleDeleteChat(chat.id, event)
                                      }
                                      onMouseLeave={() =>
                                        setConfirmDeleteChatId((current) =>
                                          current === chat.id ? null : current
                                        )
                                      }
                                      disabled={deleteChatPending}
                                      className="absolute right-1 top-1/2 z-10 inline-flex h-5 -translate-y-1/2 items-center justify-center rounded-md border border-transparent bg-transparent px-1.5 text-[10px] font-semibold leading-none text-destructive transition-colors hover:border-destructive focus-visible:border-destructive focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-destructive group-data-[collapsible=icon]:hidden"
                                      aria-label={`Confirm deleting session ${chat.title || chat.id}`}
                                    >
                                      Confirm
                                    </button>
                                  ) : (
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <SidebarMenuAction
                                          onClick={(event) =>
                                            handleRequestDeleteChat(
                                              chat.id,
                                              event
                                            )
                                          }
                                          disabled={deleteChatPending}
                                          className="pointer-events-none !top-1/2 !-translate-y-1/2 opacity-0 text-muted-foreground hover:text-foreground focus-visible:ring-ring peer-data-[active=true]/menu-button:text-muted-foreground group-hover/menu-item:pointer-events-auto group-hover/menu-item:opacity-100 data-[state=open]:pointer-events-auto data-[state=open]:opacity-100 [&>svg]:size-3"
                                          aria-label={`Delete session ${chat.title || chat.id}`}
                                        >
                                          <Trash2Icon />
                                        </SidebarMenuAction>
                                      </TooltipTrigger>
                                      <TooltipContent side="right">
                                        Delete session
                                      </TooltipContent>
                                    </Tooltip>
                                  )}
                                </SidebarMenuSubItem>
                              )
                            })}
                          </SidebarMenuSub>
                        </ScrollArea>
                      )}
                    </div>
                  </TooltipProvider>
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        )}
        {navWorkspace.some((item) => item.visible === true) && (
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
      </SidebarContent>
      <SidebarFooter>
        <SidebarUserNav />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
