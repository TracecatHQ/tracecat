"use client"

import {
  BlocksIcon,
  Bot,
  ChevronDown,
  InboxIcon,
  KeyRound,
  LayersIcon,
  LayersPlus,
  type LucideIcon,
  SquarePen,
  Table2Icon,
  UsersIcon,
  VariableIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useParams, usePathname, useRouter } from "next/navigation"
import type * as React from "react"
import { useEffect, useRef, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CreateCaseDialog } from "@/components/cases/case-create-dialog"
import { AppMenu } from "@/components/sidebar/app-menu"
import { SidebarUserNav } from "@/components/sidebar/sidebar-user-nav"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Command,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Sidebar,
  SidebarContent,
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
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"
import { shortTimeAgo } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

function SidebarHeaderContent({ workspaceId }: { workspaceId: string }) {
  return <AppMenu workspaceId={workspaceId} />
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
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const [createCaseDialogOpen, setCreateCaseDialogOpen] = useState(false)
  const canExecuteAgents = useScopeCheck("agent:execute")

  const handleNewChat = () => {
    if (canExecuteAgents !== true) {
      return
    }
    router.push(`${basePath}/copilot`)
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
  const [newAgentPrompt, setNewAgentPrompt] = useState("")
  const [newAgentPromptOpen, setNewAgentPromptOpen] = useState(false)
  const { presets, presetsIsLoading } = useAgentPresets(workspaceId, {
    enabled: agentAddonsEnabled && canViewAgents === true,
  })

  const openNewAgentBuilder = () => {
    const prompt = newAgentPrompt.trim()
    const params = prompt ? `?builderPrompt=${encodeURIComponent(prompt)}` : ""
    router.push(`${basePath}/agents/new${params}`)
    setNewAgentPrompt("")
    setNewAgentPromptOpen(false)
  }

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
  ]

  return (
    <Sidebar collapsible="offcanvas" variant="inset" {...props}>
      <SidebarHeader>
        <SidebarHeaderContent workspaceId={workspaceId} />
      </SidebarHeader>
      <SidebarContent className="overflow-y-auto">
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
              {agentAddonsEnabled && canViewAgents === true && (
                <SidebarMenuItem>
                  <Popover
                    open={newAgentPromptOpen}
                    onOpenChange={setNewAgentPromptOpen}
                  >
                    <PopoverTrigger asChild>
                      <SidebarMenuButton>
                        <Bot />
                        <span>New agent</span>
                      </SidebarMenuButton>
                    </PopoverTrigger>
                    <PopoverContent align="start" className="w-80 p-0">
                      <Command>
                        <CommandInput
                          value={newAgentPrompt}
                          onValueChange={setNewAgentPrompt}
                          placeholder="Describe the builder task..."
                        />
                        <CommandList>
                          <CommandItem
                            onSelect={openNewAgentBuilder}
                            className="py-2"
                          >
                            Start builder assistant
                          </CommandItem>
                        </CommandList>
                      </Command>
                    </PopoverContent>
                  </Popover>
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

        {agentAddonsEnabled && canViewAgents === true && (
          <Collapsible defaultOpen className="group/collapsible">
            <SidebarGroup>
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger>
                  Agents
                  <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]/collapsible:rotate-180" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <CollapsibleContent>
                <SidebarGroupContent>
                  {presetsIsLoading ? (
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
                  )}
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        )}

        <div className="mt-auto p-2">
          {canViewMembers === true && (
            <SidebarMenu className="mb-2">
              <SidebarMenuItem>
                <SidebarMenuButton
                  asChild
                  isActive={pathname?.startsWith(`${basePath}/members`)}
                >
                  <Link href={`${basePath}/members`}>
                    <UsersIcon />
                    <span>Members</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          )}
          <SidebarUserNav />
        </div>
      </SidebarContent>
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
