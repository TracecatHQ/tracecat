"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import {
  ArrowUpRight,
  BotIcon,
  ChevronRightIcon,
  PowerIcon,
  RadarIcon,
  ShieldOffIcon,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { WatchtowerAgentRead, WatchtowerAgentToolCallRead } from "@/client"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { ClaudeIcon, OpenAIIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { SlidingPanel } from "@/components/sliding-panel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useToast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useWatchtowerActions,
  useWatchtowerAgentSessions,
  useWatchtowerAgents,
  useWatchtowerSessionToolCalls,
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

export function WatchtowerMonitor() {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const watchtowerEnabled = hasEntitlement("watchtower")
  const { workspaces } = useWorkspaceManager()

  const [agentStatusFilter, setAgentStatusFilter] = useState("all")
  const [sessionStateFilter, setSessionStateFilter] = useState("all")
  const [toolStatusFilter, setToolStatusFilter] = useState("all")
  const [workspaceFilter, setWorkspaceFilter] = useState("all")

  const {
    data: agentsResponse,
    isLoading: agentsLoading,
    isFetching: agentsFetching,
  } = useWatchtowerAgents({
    limit: 200,
    status: agentStatusFilter === "all" ? undefined : agentStatusFilter,
  })

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null
  )
  const [expandedGroups, setExpandedGroups] = useState<string[]>([])

  const agents = agentsResponse?.items ?? []
  const selectedAgent =
    agents.find((agent) => agent.id === selectedAgentId) ?? null

  // Sessions for the selected agent (shown in panel)
  const { data: sessionsResponse, isLoading: sessionsLoading } =
    useWatchtowerAgentSessions(selectedAgentId, {
      limit: 200,
      state: sessionStateFilter === "all" ? undefined : sessionStateFilter,
      workspaceId: workspaceFilter === "all" ? undefined : workspaceFilter,
    })
  const sessions = sessionsResponse?.items ?? []

  useEffect(() => {
    if (
      selectedSessionId &&
      sessions.some((session) => session.id === selectedSessionId)
    ) {
      return
    }
    setSelectedSessionId(sessions[0]?.id ?? null)
  }, [sessions, selectedSessionId])

  const selectedSession =
    sessions.find((session) => session.id === selectedSessionId) ?? null

  // Tool calls for the selected session (shown in panel)
  const { data: toolCallsResponse, isLoading: toolCallsLoading } =
    useWatchtowerSessionToolCalls(selectedSessionId, {
      limit: 200,
      status: toolStatusFilter === "all" ? undefined : toolStatusFilter,
    })
  const toolCalls = toolCallsResponse?.items ?? []

  const { disableAgent, enableAgent, revokeSession } = useWatchtowerActions()

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
      if (expandedGroups.length > 0) {
        setExpandedGroups([])
      }
      return
    }
    if (expandedGroups.length === 0) {
      setExpandedGroups(groupedAgents.map((group) => group.agentType))
    }
  }, [groupedAgents, expandedGroups.length])

  const handleSelectAgent = (agentId: string) => {
    setSelectedAgentId(agentId)
    setSelectedSessionId(null)
  }

  const handlePanelClose = () => {
    setSelectedAgentId(null)
    setSelectedSessionId(null)
  }

  const handleDisableAgent = async (agent: WatchtowerAgentRead) => {
    const confirmed = window.confirm(
      `Disable this ${agentTypeConfig(agent.agent_type).label.toLowerCase()} agent?`
    )
    if (!confirmed) return
    const reason = window.prompt("Optional reason", "") ?? undefined
    try {
      await disableAgent.mutateAsync({
        agentId: agent.id,
        reason: normalizeReason(reason),
      })
      toast({ title: "Agent disabled" })
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

  const handleRevokeSession = async () => {
    if (!selectedSession) return
    const confirmed = window.confirm("Revoke this agent session?")
    if (!confirmed) return
    const reason = window.prompt("Optional reason", "") ?? undefined
    try {
      await revokeSession.mutateAsync({
        sessionId: selectedSession.id,
        reason: normalizeReason(reason),
      })
      toast({ title: "Session revoked" })
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Failed to revoke session",
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
            <SelectTrigger className="h-6 w-[150px] text-xs">
              <SelectValue placeholder="Agent status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All agent status</SelectItem>
              <SelectItem value="active">Active agents</SelectItem>
              <SelectItem value="idle">Idle agents</SelectItem>
              <SelectItem value="blocked">Blocked agents</SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={sessionStateFilter}
            onValueChange={setSessionStateFilter}
          >
            <SelectTrigger className="h-6 w-[170px] text-xs">
              <SelectValue placeholder="Session state" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All session states</SelectItem>
              <SelectItem value="awaiting_initialize">
                Awaiting initialize
              </SelectItem>
              <SelectItem value="connected">Connected</SelectItem>
              <SelectItem value="revoked">Revoked</SelectItem>
            </SelectContent>
          </Select>

          <Select value={workspaceFilter} onValueChange={setWorkspaceFilter}>
            <SelectTrigger className="h-6 w-[170px] text-xs">
              <SelectValue placeholder="Workspace" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All workspaces</SelectItem>
              {(workspaces ?? []).map((workspace) => (
                <SelectItem key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={toolStatusFilter} onValueChange={setToolStatusFilter}>
            <SelectTrigger className="h-6 w-[150px] text-xs">
              <SelectValue placeholder="Tool status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All tool status</SelectItem>
              <SelectItem value="success">Success</SelectItem>
              <SelectItem value="error">Error</SelectItem>
              <SelectItem value="timeout">Timeout</SelectItem>
              <SelectItem value="rejected">Rejected</SelectItem>
              <SelectItem value="blocked">Blocked</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Accordion body - full width, matching CasesAccordion */}
      <div className="min-h-0 flex-1">
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

      {/* Sliding panel - same component as case management */}
      <SlidingPanel
        className="py-0 sm:w-4/5 md:w-4/5 lg:w-4/5"
        isOpen={!!selectedAgentId}
        setIsOpen={(isOpen) => {
          if (!isOpen) {
            handlePanelClose()
          }
        }}
      >
        {selectedAgent && (
          <div className="flex h-full flex-col">
            {/* Agent header */}
            <div className="shrink-0 border-b px-4 py-3">
              <div className="flex items-start gap-3">
                <AgentTypeIcon agentType={selectedAgent.agent_type} size="lg" />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    {agentTypeConfig(selectedAgent.agent_type).label}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {selectedAgent.last_user_name || "Unknown user"} ·{" "}
                    {selectedAgent.last_user_email || "No email"}
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    User agent: {selectedAgent.raw_user_agent || "Not reported"}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={selectedAgent.status} />
                  {selectedAgent.status === "blocked" ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={enableAgent.isPending}
                      onClick={() => handleEnableAgent(selectedAgent)}
                    >
                      <PowerIcon className="mr-1.5 size-3.5" />
                      Enable
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={disableAgent.isPending}
                      onClick={() => handleDisableAgent(selectedAgent)}
                    >
                      <ShieldOffIcon className="mr-1.5 size-3.5" />
                      Disable
                    </Button>
                  )}
                </div>
              </div>
            </div>

            {/* Sessions & Tool calls - split layout inside panel */}
            <div className="min-h-0 flex-1 overflow-hidden lg:grid lg:grid-cols-[340px_minmax(0,1fr)]">
              {/* Sessions list */}
              <div className="min-h-0 border-b lg:border-b-0 lg:border-r">
                <div className="flex h-8 items-center border-b px-3">
                  <span className="text-xs font-medium text-muted-foreground">
                    Sessions
                  </span>
                  {selectedSession && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="ml-auto h-6 px-2 text-xs"
                      disabled={revokeSession.isPending}
                      onClick={handleRevokeSession}
                    >
                      Revoke
                    </Button>
                  )}
                </div>

                <div className="min-h-0 h-[calc(100%-2rem)] overflow-auto">
                  {sessionsLoading ? (
                    <div className="flex h-full items-center justify-center">
                      <CenteredSpinner />
                    </div>
                  ) : sessions.length === 0 ? (
                    <div className="px-3 py-6 text-sm text-muted-foreground">
                      No sessions for this agent.
                    </div>
                  ) : (
                    sessions.map((session) => (
                      <button
                        key={session.id}
                        type="button"
                        onClick={() => setSelectedSessionId(session.id)}
                        className={cn(
                          "w-full border-l-2 border-l-transparent border-b border-border/50 px-3 py-2 text-left transition-colors hover:bg-muted/50",
                          selectedSessionId === session.id &&
                            "border-l-foreground/40 bg-muted/40"
                        )}
                      >
                        <div className="flex items-center gap-2">
                          <div className="min-w-0 flex-1 truncate text-xs font-medium">
                            {session.user_email || "No email"}
                          </div>
                          <StatusBadge status={session.status} />
                        </div>
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {shortId(session.id)} ·{" "}
                          {formatRelative(session.last_seen_at)}
                        </div>
                      </button>
                    ))
                  )}
                </div>
              </div>

              {/* Tool calls */}
              <div className="min-h-0 flex flex-col">
                <div className="flex h-8 items-center border-b px-3">
                  <span className="text-xs font-medium text-muted-foreground">
                    Tool calls
                  </span>
                  {selectedSession && (
                    <span className="ml-auto text-[11px] text-muted-foreground">
                      Session {shortId(selectedSession.id)}
                    </span>
                  )}
                </div>

                <div className="min-h-0 flex-1 overflow-auto">
                  {toolCallsLoading ? (
                    <div className="flex h-full items-center justify-center">
                      <CenteredSpinner />
                    </div>
                  ) : selectedSession == null ? (
                    <div className="px-3 py-6 text-sm text-muted-foreground">
                      Select a session to view tool calls.
                    </div>
                  ) : toolCalls.length === 0 ? (
                    <div className="px-3 py-6 text-sm text-muted-foreground">
                      No tool calls captured for this session.
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
      </SlidingPanel>
    </div>
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
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <StatusBadge status={agent.status} />
        <span className="text-[11px] text-muted-foreground">
          {formatRelative(agent.last_seen_at)}
        </span>
      </div>
    </button>
  )
}

function StatusBadge({ status }: { status: string }) {
  if (status === "blocked" || status === "revoked") {
    return (
      <Badge variant="destructive" className="h-5 px-1.5 text-[10px] uppercase">
        {status}
      </Badge>
    )
  }
  if (status === "active" || status === "success") {
    return (
      <Badge variant="secondary" className="h-5 px-1.5 text-[10px] uppercase">
        {status}
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="h-5 px-1.5 text-[10px] uppercase">
      {status}
    </Badge>
  )
}

function ToolCallRow({ toolCall }: { toolCall: WatchtowerAgentToolCallRead }) {
  return (
    <div className="border-b border-border/50 px-3 py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1 truncate text-xs font-medium">
          {toolCall.tool_name}
        </div>
        <StatusBadge status={toolCall.call_status} />
      </div>
      <div className="mt-1 text-[11px] text-muted-foreground">
        {formatRelative(toolCall.called_at)}
        {typeof toolCall.latency_ms === "number"
          ? ` · ${toolCall.latency_ms}ms`
          : ""}
      </div>
    </div>
  )
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

function normalizeReason(value: string | undefined): string | undefined {
  if (!value) {
    return undefined
  }
  const normalized = value.trim()
  return normalized.length > 0 ? normalized : undefined
}
