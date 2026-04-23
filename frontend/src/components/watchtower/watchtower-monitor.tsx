"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import type { ToolUIPart } from "ai"
import {
  ArrowUpRight,
  BotIcon,
  Building2Icon,
  ChevronRightIcon,
  CircleCheckIcon,
  CircleIcon,
  Clock3Icon,
  type LucideIcon,
  RadarIcon,
  ShieldOffIcon,
  SlidersHorizontalIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react"
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react"
import type { WatchtowerAgentRead, WatchtowerAgentToolCallRead } from "@/client"
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { ClaudeIcon, getIcon, OpenAIIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { CenteredSpinner } from "@/components/loading/spinner"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useWatchtowerActions,
  useWatchtowerAgents,
  useWatchtowerAgentToolCalls,
} from "@/hooks/use-watchtower"
import { getRelativeTime } from "@/lib/event-history"
import { useWorkspaceManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"

const AGENT_TYPE_ORDER = [
  "claude_code",
  "codex",
  "cursor",
  "windsurf",
  "opencode",
  "openclaw",
  "unknown",
] as const

interface AgentTypeGroupConfig {
  label: string
  iconColor: string
  triggerClassName: string
}

const AGENT_TYPE_GROUPS: Record<string, AgentTypeGroupConfig> = {
  claude_code: {
    label: "Claude Code",
    iconColor: "text-amber-600",
    triggerClassName:
      "data-[state=open]:border-l-amber-600 data-[state=open]:bg-amber-600/[0.03] dark:data-[state=open]:bg-amber-600/[0.08]",
  },
  codex: {
    label: "Codex",
    iconColor: "text-green-600",
    triggerClassName:
      "data-[state=open]:border-l-green-600 data-[state=open]:bg-green-600/[0.03] dark:data-[state=open]:bg-green-600/[0.08]",
  },
  cursor: {
    label: "Cursor",
    iconColor: "text-blue-600",
    triggerClassName:
      "data-[state=open]:border-l-blue-600 data-[state=open]:bg-blue-600/[0.03] dark:data-[state=open]:bg-blue-600/[0.08]",
  },
  windsurf: {
    label: "Windsurf",
    iconColor: "text-violet-600",
    triggerClassName:
      "data-[state=open]:border-l-violet-600 data-[state=open]:bg-violet-600/[0.03] dark:data-[state=open]:bg-violet-600/[0.08]",
  },
  opencode: {
    label: "OpenCode",
    iconColor: "text-slate-600",
    triggerClassName:
      "data-[state=open]:border-l-slate-600 data-[state=open]:bg-slate-600/[0.03] dark:data-[state=open]:bg-slate-600/[0.08]",
  },
  openclaw: {
    label: "OpenClaw",
    iconColor: "text-rose-600",
    triggerClassName:
      "data-[state=open]:border-l-rose-600 data-[state=open]:bg-rose-600/[0.03] dark:data-[state=open]:bg-rose-600/[0.08]",
  },
  unknown: {
    label: "Unknown",
    iconColor: "text-muted-foreground",
    triggerClassName:
      "data-[state=open]:border-l-muted-foreground data-[state=open]:bg-muted/50",
  },
}

function agentTypeConfig(agentType: string): AgentTypeGroupConfig {
  return AGENT_TYPE_GROUPS[agentType] ?? AGENT_TYPE_GROUPS.unknown
}

const TOOL_ARGS_PREVIEW_LENGTH = 180

interface FilterSelectOption {
  value: string
  label: string
  icon: LucideIcon
  iconClassName?: string
}

const AGENT_STATUS_FILTER_OPTIONS: FilterSelectOption[] = [
  {
    value: "all",
    label: "All agent status",
    icon: SlidersHorizontalIcon,
  },
  {
    value: "active",
    label: "Active agents",
    icon: CircleCheckIcon,
    iconClassName: "text-emerald-600",
  },
  {
    value: "idle",
    label: "Idle agents",
    icon: CircleIcon,
  },
  {
    value: "blocked",
    label: "Blocked agents",
    icon: ShieldOffIcon,
    iconClassName: "text-destructive",
  },
]

const TOOL_STATUS_FILTER_OPTIONS: FilterSelectOption[] = [
  {
    value: "all",
    label: "All tool status",
    icon: SlidersHorizontalIcon,
  },
  {
    value: "success",
    label: "Success",
    icon: CircleCheckIcon,
    iconClassName: "text-emerald-600",
  },
  {
    value: "error",
    label: "Error",
    icon: XIcon,
    iconClassName: "text-destructive",
  },
  {
    value: "timeout",
    label: "Timeout",
    icon: Clock3Icon,
    iconClassName: "text-amber-600",
  },
  {
    value: "rejected",
    label: "Rejected",
    icon: XIcon,
    iconClassName: "text-destructive",
  },
  {
    value: "blocked",
    label: "Blocked",
    icon: ShieldOffIcon,
    iconClassName: "text-destructive",
  },
]

export function WatchtowerMonitor() {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const watchtowerEnabled = hasEntitlement("watchtower")
  const { workspaces } = useWorkspaceManager()

  const [agentStatusFilter, setAgentStatusFilter] = useState("all")
  const [toolStatusFilter, setToolStatusFilter] = useState("all")
  const [workspaceFilter, setWorkspaceFilter] = useState("all")

  const {
    data: agentsResponse,
    isLoading: agentsLoading,
    isFetching: agentsFetching,
  } = useWatchtowerAgents({
    limit: 200,
    status:
      agentStatusFilter === "all"
        ? undefined
        : (agentStatusFilter as WatchtowerAgentRead["status"]),
  })

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [expandedGroups, setExpandedGroups] = useState<string[]>([])
  const [disableDialogAgent, setDisableDialogAgent] =
    useState<WatchtowerAgentRead | null>(null)
  const [disableReason, setDisableReason] = useState("")
  const didAutoExpandGroupsRef = useRef(false)

  const agents = agentsResponse?.items ?? []
  const selectedAgent =
    agents.find((agent) => agent.id === selectedAgentId) ?? null

  // Tool calls for the selected agent (fanned out across all duplicate
  // fingerprints in the same email + harness group on the backend).
  const { data: toolCallsResponse, isLoading: toolCallsLoading } =
    useWatchtowerAgentToolCalls(selectedAgentId, {
      limit: 200,
      status:
        toolStatusFilter === "all"
          ? undefined
          : (toolStatusFilter as WatchtowerAgentToolCallRead["call_status"]),
    })
  const toolCalls = toolCallsResponse?.items ?? []

  const { disableAgent, enableAgent } = useWatchtowerActions()

  const selectedAgentStatusFilter = findFilterSelectOption(
    AGENT_STATUS_FILTER_OPTIONS,
    agentStatusFilter
  )
  const selectedToolStatusFilter = findFilterSelectOption(
    TOOL_STATUS_FILTER_OPTIONS,
    toolStatusFilter
  )
  const selectedWorkspaceName =
    workspaceFilter === "all"
      ? "All workspaces"
      : ((workspaces ?? []).find(
          (workspace) => workspace.id === workspaceFilter
        )?.name ?? "Workspace")

  const groupedAgents = useMemo(() => {
    const grouped = new Map<string, WatchtowerAgentRead[]>()
    for (const agent of agents) {
      const key = agent.agent_type || "unknown"
      const next = grouped.get(key) ?? []
      next.push(agent)
      grouped.set(key, next)
    }

    return AGENT_TYPE_ORDER.map((agentType) => ({
      agentType,
      items: grouped.get(agentType) ?? [],
    })).filter((group) => group.items.length > 0)
  }, [agents])

  useEffect(() => {
    if (groupedAgents.length === 0) {
      didAutoExpandGroupsRef.current = false
      if (expandedGroups.length > 0) {
        setExpandedGroups([])
      }
      return
    }
    if (!didAutoExpandGroupsRef.current && expandedGroups.length === 0) {
      didAutoExpandGroupsRef.current = true
      setExpandedGroups(groupedAgents.map((group) => group.agentType))
    }
  }, [groupedAgents, expandedGroups.length])

  const handleSelectAgent = (agentId: string) => {
    setSelectedAgentId(agentId)
  }

  const handlePanelClose = () => {
    setSelectedAgentId(null)
  }

  const handleDisableAgent = async () => {
    if (!disableDialogAgent || disableAgent.isPending) return
    try {
      await disableAgent.mutateAsync({
        agentId: disableDialogAgent.id,
        reason: normalizeReason(disableReason),
      })
      toast({ title: "Agent disabled" })
      setDisableDialogAgent(null)
      setDisableReason("")
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Failed to disable agent",
        description: String(error),
      })
    }
  }

  const handleEnableAgent = async (agent: WatchtowerAgentRead) => {
    try {
      await enableAgent.mutateAsync({ agentId: agent.id })
      toast({ title: "Agent enabled" })
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Failed to enable agent",
        description: String(error),
      })
    }
  }

  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  if (!watchtowerEnabled) {
    return (
      <div className="flex size-full items-center justify-center">
        <EntitlementRequiredEmptyState
          title="Upgrade required"
          description="Watchtower monitoring is unavailable on your current plan."
        >
          <Button
            variant="link"
            asChild
            className="text-muted-foreground"
            size="sm"
          >
            <a
              href="https://tracecat.com"
              target="_blank"
              rel="noopener noreferrer"
            >
              Learn more <ArrowUpRight className="size-4" />
            </a>
          </Button>
        </EntitlementRequiredEmptyState>
      </div>
    )
  }

  return (
    <>
      <div className="flex size-full flex-col">
        {/* Header - matches CasesHeader structure */}
        <div className="shrink-0 border-b">
          <header className="flex h-10 items-center border-b pl-3 pr-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                <RadarIcon className="size-4 text-muted-foreground" />
              </div>
              <span className="text-sm font-medium">Monitor</span>
            </div>
            <div className="ml-auto">
              <span className="text-xs text-muted-foreground">
                {agentsFetching
                  ? "Refreshing..."
                  : `${agents.length} agent${agents.length === 1 ? "" : "s"} · Live every 5s`}
              </span>
            </div>
          </header>

          <div className="flex flex-wrap items-center gap-2 py-2 pl-3 pr-4">
            <Select
              value={agentStatusFilter}
              onValueChange={setAgentStatusFilter}
            >
              <SelectTrigger className="h-6 w-[180px] text-xs">
                <SelectValue>
                  <FilterSelectValueContent
                    option={selectedAgentStatusFilter}
                    triggerIcon={BotIcon}
                  />
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {AGENT_STATUS_FILTER_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    <FilterSelectOptionContent option={option} />
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={workspaceFilter} onValueChange={setWorkspaceFilter}>
              <SelectTrigger className="h-6 w-[190px] text-xs">
                <SelectValue>
                  <span className="flex items-center gap-2">
                    <Building2Icon className="size-3.5 text-muted-foreground" />
                    <span className="truncate">{selectedWorkspaceName}</span>
                  </span>
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  <span className="flex items-center gap-2">
                    <SlidersHorizontalIcon className="size-3.5 text-muted-foreground" />
                    <span>All workspaces</span>
                  </span>
                </SelectItem>
                {(workspaces ?? []).map((workspace) => (
                  <SelectItem key={workspace.id} value={workspace.id}>
                    <span className="flex items-center gap-2">
                      <Building2Icon className="size-3.5 text-muted-foreground" />
                      <span>{workspace.name}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={toolStatusFilter}
              onValueChange={setToolStatusFilter}
            >
              <SelectTrigger className="h-6 w-[170px] text-xs">
                <SelectValue>
                  <FilterSelectValueContent
                    option={selectedToolStatusFilter}
                    triggerIcon={WrenchIcon}
                  />
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {TOOL_STATUS_FILTER_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    <FilterSelectOptionContent option={option} />
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Content area: rows list + inline details panel */}
        <div className="min-h-0 flex-1">
          <div
            className={cn(
              "grid h-full min-h-0",
              selectedAgent
                ? "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
                : "grid-cols-1"
            )}
          >
            <div className="min-h-0">
              {agentsLoading ? (
                <div className="flex h-full items-center justify-center">
                  <CenteredSpinner />
                </div>
              ) : groupedAgents.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  No local agent sessions observed yet.
                </div>
              ) : (
                <div className="h-full overflow-auto">
                  <AccordionPrimitive.Root
                    type="multiple"
                    value={expandedGroups}
                    onValueChange={setExpandedGroups}
                    className="w-full"
                  >
                    {groupedAgents.map((group) => {
                      const config = agentTypeConfig(group.agentType)
                      return (
                        <AccordionPrimitive.Item
                          key={group.agentType}
                          value={group.agentType}
                          className="group/accordion border-b border-border/50"
                          data-agent-type={group.agentType}
                        >
                          <AccordionPrimitive.Header className="flex">
                            <AccordionPrimitive.Trigger
                              className={cn(
                                "flex w-full items-center gap-1 border-l-2 border-l-transparent py-1.5 pl-[10px] pr-3 text-left transition-colors",
                                "hover:bg-muted/50",
                                "[&[data-state=open]_.chevron]:rotate-90",
                                "data-[state=open]:border-l-current",
                                config.triggerClassName
                              )}
                            >
                              <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                                <ChevronRightIcon className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                              </div>
                              <div className="flex items-center gap-1.5">
                                <AgentTypeIcon agentType={group.agentType} />
                                <span className="text-xs font-medium">
                                  {config.label}
                                </span>
                                <span className="text-xs text-muted-foreground">
                                  {group.items.length}
                                </span>
                              </div>
                            </AccordionPrimitive.Trigger>
                          </AccordionPrimitive.Header>
                          <AccordionPrimitive.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
                            <div className="ml-[18px]">
                              {group.items.map((agent) => (
                                <AgentRow
                                  key={agent.id}
                                  agent={agent}
                                  isSelected={selectedAgentId === agent.id}
                                  onClick={() => handleSelectAgent(agent.id)}
                                />
                              ))}
                            </div>
                          </AccordionPrimitive.Content>
                        </AccordionPrimitive.Item>
                      )
                    })}
                  </AccordionPrimitive.Root>
                </div>
              )}
            </div>

            {selectedAgent && (
              <div className="min-h-0 border-t lg:border-l lg:border-t-0">
                <div className="flex h-full min-h-0 flex-col">
                  {/* Agent header */}
                  <div className="shrink-0 border-b px-4 py-3">
                    <div className="flex items-start gap-3">
                      <AgentTypeIcon
                        agentType={selectedAgent.agent_type}
                        size="lg"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">
                          {agentTypeConfig(selectedAgent.agent_type).label}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {selectedAgent.last_user_name || "Unknown user"} ·{" "}
                          {selectedAgent.last_user_email || "No email"}
                        </div>
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          User agent:{" "}
                          {selectedAgent.raw_user_agent || "Not reported"}
                        </div>
                        {selectedAgent.status === "blocked" &&
                        selectedAgent.blocked_reason ? (
                          <div className="mt-1 text-[11px] text-muted-foreground">
                            Blocked reason:{" "}
                            <span className="text-foreground">
                              {selectedAgent.blocked_reason}
                            </span>
                          </div>
                        ) : null}
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusBadge
                          status={selectedAgent.status}
                          reason={selectedAgent.blocked_reason}
                        />
                        {selectedAgent.status === "blocked" ? (
                          <button
                            type="button"
                            disabled={enableAgent.isPending}
                            onClick={() => handleEnableAgent(selectedAgent)}
                            className="inline-flex h-5 items-center rounded-md border bg-background px-1.5 text-[10px] font-medium uppercase text-foreground transition-colors hover:bg-muted disabled:opacity-50"
                          >
                            Enable
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={disableAgent.isPending}
                            onClick={() => {
                              setDisableDialogAgent(selectedAgent)
                              setDisableReason("")
                            }}
                            className="inline-flex h-5 items-center rounded-md border border-transparent bg-destructive px-1.5 text-[10px] font-medium uppercase text-destructive-foreground transition-colors hover:bg-destructive/90 disabled:opacity-50"
                          >
                            Disable
                          </button>
                        )}
                        <Button
                          size="icon"
                          variant="ghost"
                          className="size-7"
                          onClick={handlePanelClose}
                        >
                          <XIcon className="size-4" />
                          <span className="sr-only">Close details</span>
                        </Button>
                      </div>
                    </div>
                  </div>

                  {/* Tool calls take the full panel below the agent header. */}
                  <div className="min-h-0 flex flex-1 flex-col">
                    <div className="flex h-8 shrink-0 items-center border-b px-3">
                      <span className="text-xs font-medium text-muted-foreground">
                        Tool calls
                      </span>
                    </div>

                    <div className="min-h-0 flex-1 overflow-auto">
                      {toolCallsLoading ? (
                        <div className="flex h-full items-center justify-center">
                          <CenteredSpinner />
                        </div>
                      ) : toolCalls.length === 0 ? (
                        <div className="px-3 py-6 text-sm text-muted-foreground">
                          No tool calls captured for this agent.
                        </div>
                      ) : (
                        toolCalls.map((toolCall) => (
                          <ToolCallRow key={toolCall.id} toolCall={toolCall} />
                        ))
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <AlertDialog
        open={disableDialogAgent !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDisableDialogAgent(null)
            setDisableReason("")
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disable local agent</AlertDialogTitle>
            <AlertDialogDescription>
              {disableDialogAgent
                ? `Disable ${agentTypeConfig(disableDialogAgent.agent_type).label} for ${disableDialogAgent.last_user_email || "this user"}?`
                : "Disable this local agent?"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label
              htmlFor="watchtower-disable-reason"
              className="text-sm text-muted-foreground"
            >
              Optional reason
            </label>
            <Input
              id="watchtower-disable-reason"
              placeholder="Reason shown in audit logs"
              value={disableReason}
              onChange={(event) => setDisableReason(event.target.value)}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={disableAgent.isPending}
              onClick={(event) => {
                event.preventDefault()
                void handleDisableAgent()
              }}
            >
              Disable
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function findFilterSelectOption(
  options: FilterSelectOption[],
  value: string
): FilterSelectOption {
  return options.find((option) => option.value === value) ?? options[0]
}

function FilterSelectOptionContent({ option }: { option: FilterSelectOption }) {
  const Icon = option.icon
  return (
    <span className="flex items-center gap-2">
      <Icon
        className={cn("size-3.5 text-muted-foreground", option.iconClassName)}
      />
      <span>{option.label}</span>
    </span>
  )
}

function FilterSelectValueContent({
  option,
  triggerIcon: TriggerIcon,
}: {
  option: FilterSelectOption
  triggerIcon: LucideIcon
}) {
  return (
    <span className="flex items-center gap-2">
      <TriggerIcon className="size-3.5 text-muted-foreground" />
      <span className="truncate">{option.label}</span>
    </span>
  )
}

/** Single agent row in the accordion - styled identically to CaseItem */
function AgentRow({
  agent,
  isSelected,
  onClick,
}: {
  agent: WatchtowerAgentRead
  isSelected: boolean
  onClick: () => void
}) {
  const activeSessionCount = agent.active_session_count ?? 0
  const inactiveSessionCount = agent.inactive_session_count ?? 0

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group/item",
        "-ml-[18px] flex w-[calc(100%+18px)] items-center gap-3 py-2 pl-3 pr-3 text-left transition-colors",
        "hover:bg-muted/50",
        isSelected && "bg-muted"
      )}
    >
      <AgentTypeIcon agentType={agent.agent_type} />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="shrink-0 text-xs font-medium text-muted-foreground">
          {agent.last_user_name || "Unknown user"}
        </span>
        <span className="truncate text-xs">
          {agent.last_user_email || "No email"}
        </span>
        {activeSessionCount > 0 || inactiveSessionCount > 0 ? (
          <span className="inline-flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
            {activeSessionCount > 0 ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex items-center gap-1">
                    <CircleIcon className="size-2 fill-emerald-500 text-emerald-500" />
                    {activeSessionCount}
                  </span>
                </TooltipTrigger>
                <TooltipContent>Active sessions</TooltipContent>
              </Tooltip>
            ) : null}
            {inactiveSessionCount > 0 ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex items-center gap-1">
                    <CircleIcon className="size-2 fill-current" />
                    {inactiveSessionCount}
                  </span>
                </TooltipTrigger>
                <TooltipContent>Inactive sessions</TooltipContent>
              </Tooltip>
            ) : null}
          </span>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <StatusBadge
          status={agent.status}
          reason={agent.status === "blocked" ? agent.blocked_reason : undefined}
        />
        <span className="text-[11px] text-muted-foreground">
          {capitalizeFirst(formatRelative(agent.last_seen_at))}
        </span>
      </div>
    </button>
  )
}

function StatusBadge({
  status,
  reason,
}: {
  status: string
  reason?: string | null
}) {
  const config = statusBadgeConfig(status)
  const content = (
    <Badge
      variant={config.variant}
      className="h-5 px-1.5 text-[10px] uppercase"
    >
      <span className="mr-1 shrink-0">{config.icon}</span>
      <span>{humanizeStatus(status)}</span>
    </Badge>
  )

  if (status === "blocked" && reason) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{content}</TooltipTrigger>
        <TooltipContent className="max-w-xs break-words text-xs">
          Blocked: {reason}
        </TooltipContent>
      </Tooltip>
    )
  }

  return content
}

function ToolCallRow({ toolCall }: { toolCall: WatchtowerAgentToolCallRead }) {
  const [showArgsDialog, setShowArgsDialog] = useState(false)
  const toolName = normalizeToolName(toolCall.tool_name)
  const argsPreview = truncateText(
    toCompactJsonString(toolCall.args_redacted),
    TOOL_ARGS_PREVIEW_LENGTH
  )

  return (
    <>
      <Tool className="mb-0 rounded-none border-x-0 border-t-0 px-0 last:border-b-0">
        <ToolHeader
          title={toolName}
          type={"tool-watchtower" as ToolUIPart["type"]}
          state={mapCallStatusToToolState(toolCall.call_status)}
          icon={getIcon(toolName, {
            className: "size-4 p-[3px]",
          })}
          className="px-3 py-2"
        />
        <div className="flex flex-wrap items-center gap-2 px-3 pb-2 text-[11px] text-muted-foreground">
          <span>{capitalizeFirst(formatRelative(toolCall.called_at))}</span>
          {toolCall.agent_session_id ? (
            <span>· session {shortId(toolCall.agent_session_id)}</span>
          ) : null}
          {typeof toolCall.latency_ms === "number" ? (
            <span>· {toolCall.latency_ms}ms</span>
          ) : null}
        </div>
        <ToolContent>
          <div className="space-y-3 px-3 pb-3">
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
                Args
              </div>
              <code className="block max-h-24 overflow-auto rounded-md border bg-muted/40 px-2 py-1 font-mono text-[11px]">
                {argsPreview}
              </code>
            </div>
            <div className="flex items-center justify-between gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={() => setShowArgsDialog(true)}
              >
                View full args
              </Button>
              {toolCall.error_redacted ? (
                <span className="truncate text-[11px] text-destructive">
                  {toolCall.error_redacted}
                </span>
              ) : null}
            </div>
          </div>
        </ToolContent>
      </Tool>

      <Dialog open={showArgsDialog} onOpenChange={setShowArgsDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-base">{toolName}</DialogTitle>
            <DialogDescription>
              Full redacted arguments captured for this tool call.
            </DialogDescription>
          </DialogHeader>
          <JsonViewWithControls
            src={toolCall.args_redacted}
            defaultExpanded
            defaultTab="nested"
            className="max-h-[60vh] overflow-auto"
          />
        </DialogContent>
      </Dialog>
    </>
  )
}

function statusBadgeConfig(status: string): {
  variant: "secondary" | "destructive" | "outline"
  icon: ReactNode
} {
  if (status === "blocked" || status === "revoked") {
    return {
      variant: "destructive",
      icon: <ShieldOffIcon className="size-3" />,
    }
  }
  if (status === "active") {
    return {
      variant: "secondary",
      icon: <CircleIcon className="size-2 fill-emerald-500 text-emerald-500" />,
    }
  }
  if (status === "success" || status === "connected") {
    return {
      variant: "secondary",
      icon: <CircleCheckIcon className="size-3" />,
    }
  }
  if (status === "idle") {
    return {
      variant: "outline",
      icon: <CircleIcon className="size-2 fill-current" />,
    }
  }
  if (status === "awaiting_initialize") {
    return {
      variant: "outline",
      icon: <Clock3Icon className="size-3" />,
    }
  }
  if (status === "error" || status === "timeout" || status === "rejected") {
    return {
      variant: "destructive",
      icon: <XIcon className="size-3" />,
    }
  }
  return {
    variant: "outline",
    icon: <CircleIcon className="size-2 fill-current" />,
  }
}

function mapCallStatusToToolState(callStatus: string): ToolUIPart["state"] {
  if (callStatus === "success") {
    return "output-available"
  }
  if (
    callStatus === "error" ||
    callStatus === "timeout" ||
    callStatus === "blocked" ||
    callStatus === "rejected"
  ) {
    return "output-error"
  }
  return "input-available"
}

function humanizeStatus(status: string): string {
  return status.replaceAll("_", " ")
}

function normalizeToolName(toolName: string): string {
  return toolName.replaceAll("__", ".")
}

function toCompactJsonString(value: unknown): string {
  try {
    return JSON.stringify(value) ?? "null"
  } catch {
    return String(value)
  }
}

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value
  }
  return `${value.slice(0, maxLength)}...`
}

function AgentTypeIcon({
  agentType,
  size = "default",
}: {
  agentType: string
  size?: "default" | "lg"
}) {
  const className = size === "lg" ? "size-6" : "size-4"
  if (agentType === "claude_code") {
    return <ClaudeIcon className={className} />
  }
  if (agentType === "codex") {
    return <OpenAIIcon className={className} />
  }
  if (agentType === "cursor") {
    return <MonogramIcon label="C" size={size} />
  }
  if (agentType === "windsurf") {
    return <MonogramIcon label="W" size={size} />
  }
  if (agentType === "opencode") {
    return <MonogramIcon label="O" size={size} />
  }
  if (agentType === "openclaw") {
    return <MonogramIcon label="OC" size={size} />
  }
  return <BotIcon className={className} />
}

function MonogramIcon({
  label,
  size,
}: {
  label: string
  size: "default" | "lg"
}) {
  return (
    <span
      className={cn(
        "flex items-center justify-center border text-[10px] font-semibold",
        size === "lg" ? "size-6" : "size-4"
      )}
    >
      {label}
    </span>
  )
}

function shortId(value: string) {
  if (value.length <= 8) {
    return value
  }
  return `${value.slice(0, 8)}...`
}

function formatRelative(value: string) {
  return getRelativeTime(new Date(value))
}

function capitalizeFirst(value: string): string {
  if (value.length === 0) {
    return value
  }
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function normalizeReason(value: string | undefined): string | undefined {
  if (!value) {
    return undefined
  }
  const normalized = value.trim()
  return normalized.length > 0 ? normalized : undefined
}
