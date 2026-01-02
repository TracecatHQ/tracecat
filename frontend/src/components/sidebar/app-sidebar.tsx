"use client"

import {
  ChevronDown,
  type LucideIcon,
  MoreHorizontal,
  PencilIcon,
  Plus,
  Settings2Icon,
  SquarePlus,
  SquareStackIcon,
  Table2Icon,
  TrashIcon,
  UserCheckIcon,
  UsersIcon,
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
import { useCallback, useEffect, useRef, useState } from "react"
import type { ChatRead } from "@/client"
import { AppMenu } from "@/components/sidebar/app-menu"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
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
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { useAgentPresets } from "@/hooks"
import {
  useCreateChat,
  useDeleteChat,
  useListChats,
  useUpdateChat,
} from "@/hooks/use-chat"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceId } from "@/providers/workspace-id"

function ChatSidebarItem({
  chat,
  workspaceId,
  isActive,
  basePath,
}: {
  chat: ChatRead
  workspaceId: string
  isActive: boolean
  basePath: string
}) {
  const router = useRouter()
  const { updateChat } = useUpdateChat(workspaceId)
  const { deleteChat } = useDeleteChat(workspaceId)
  const [isRenaming, setIsRenaming] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [newName, setNewName] = useState(chat.title)

  const handleRename = async () => {
    try {
      await updateChat({ chatId: chat.id, update: { title: newName } })
      setIsRenaming(false)
    } catch (error) {
      console.error("Failed to rename chat:", error)
    }
  }

  const handleDelete = async () => {
    try {
      await deleteChat({ chatId: chat.id })
      setIsDeleting(false)
      router.push(`${basePath}/copilot`)
    } catch (error) {
      console.error("Failed to delete chat:", error)
    }
  }

  return (
    <>
      <SidebarMenuItem>
        <SidebarMenuButton asChild isActive={isActive}>
          <Link href={`${basePath}/copilot?chatId=${chat.id}`}>
            <span className="truncate">{chat.title || "Untitled Chat"}</span>
          </Link>
        </SidebarMenuButton>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuAction showOnHover>
              <MoreHorizontal />
              <span className="sr-only">More</span>
            </SidebarMenuAction>
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-48" side="bottom" align="end">
            <DropdownMenuItem onClick={() => setIsRenaming(true)}>
              <PencilIcon className="mr-2 size-3.5" />
              <span>Rename</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setIsDeleting(true)}
              className="text-destructive focus:text-destructive"
            >
              <TrashIcon className="mr-2 size-3.5" />
              <span>Delete</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>

      <Dialog open={isRenaming} onOpenChange={setIsRenaming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename Chat</DialogTitle>
          </DialogHeader>
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRename()
            }}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsRenaming(false)}>
              Cancel
            </Button>
            <Button onClick={handleRename}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={isDeleting} onOpenChange={setIsDeleting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete chat?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete the chat "{chat.title}". This action
              cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

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
  const { isFeatureEnabled } = useFeatureFlag()
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const { presets } = useAgentPresets(workspaceId, {
    enabled: agentPresetsEnabled,
  })

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
    {
      title: "Tables",
      url: `${basePath}/tables`,
      icon: Table2Icon,
      isActive: pathname?.startsWith(`${basePath}/tables`),
    },
    {
      title: "Configs",
      icon: Settings2Icon,
      items: [
        {
          title: "Variables",
          url: `${basePath}/variables`,
          isActive: pathname?.startsWith(`${basePath}/variables`),
        },
        {
          title: "Credentials",
          url: `${basePath}/credentials`,
          isActive: pathname?.startsWith(`${basePath}/credentials`),
        },
        {
          title: "Integrations",
          url: `${basePath}/integrations`,
          isActive: pathname?.startsWith(`${basePath}/integrations`),
        },
      ],
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
                  {navWorkspace.map((item) => (
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
        {agentPresetsEnabled ? (
          <Collapsible defaultOpen className="group/collapsible">
            <SidebarGroup className="group/label">
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger>
                  Agents
                  <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <SidebarGroupAction
                title="New agent"
                asChild
                className="mr-5 opacity-0 transition-opacity group-hover/label:opacity-100"
              >
                <Link href={`${basePath}/agents/new`}>
                  <Plus /> <span className="sr-only">New agent</span>
                </Link>
              </SidebarGroupAction>
              <CollapsibleContent>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {presets?.map((preset) => (
                      <SidebarMenuItem key={preset.id}>
                        <SidebarMenuButton
                          asChild
                          isActive={pathname?.includes(`/agents/${preset.id}`)}
                        >
                          <Link href={`${basePath}/agents/${preset.id}`}>
                            <span className="truncate">{preset.name}</span>
                          </Link>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    ))}
                  </SidebarMenu>
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        ) : null}
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
                    <ChatSidebarItem
                      key={chat.id}
                      chat={chat}
                      workspaceId={workspaceId}
                      basePath={basePath}
                      isActive={
                        pathname === `${basePath}/copilot` &&
                        searchParams?.get("chatId") === chat.id
                      }
                    />
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
