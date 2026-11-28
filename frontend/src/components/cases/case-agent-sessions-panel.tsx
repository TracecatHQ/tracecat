"use client"

import {
  ChevronRightIcon,
  CirclePauseIcon,
  LoaderCircleIcon,
  MessageSquareIcon,
  SparklesIcon,
  XCircleIcon,
} from "lucide-react"
import { memo, useCallback, useMemo, useRef, useState } from "react"
import {
  type MCPIntegrationRead,
  mcpIntegrationsListMcpIntegrations,
} from "@/client"
import { OverflowTooltipList } from "@/components/agents/overflow-tooltip-list"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  TranscriptOutlineContext,
  type TranscriptOutlineEntry,
} from "@/components/chat/transcript-outline"
import { getIcon, getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import { InboxChat } from "@/components/inbox/inbox-chat"
import { Spinner } from "@/components/loading/spinner"
import { UpdatedAtTimestamp } from "@/components/timestamps"
import { Button } from "@/components/ui/button"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useCaseAgentSessions } from "@/hooks/use-inbox"
import { type InboxSessionItem, isLiveAgentStatus } from "@/lib/agents"
import { useQuery } from "@/lib/query"
import { cn, reconstructActionType } from "@/lib/utils"

/** Props for {@link CaseAgentSessionsPanel}. */
export interface CaseAgentSessionsPanelProps {
  caseId: string
  workspaceId: string
  /** Embedded (chat artifact): the list stacks above the transcript. */
  embedded?: boolean
}

/** Tool icons shown on a row before the rest collapse into a `+N`. */
const MAX_TOOL_ICONS = 2

interface SessionToolIcon {
  id: string
  label: string
  iconKey?: string
  providerId?: string
}

type McpIntegrationMeta = { name: string; slug: string }

/**
 * The tools and MCP servers a session was given, one icon per tool namespace
 * so ten actions from one integration do not repeat its logo.
 */
function getSessionToolIcons(
  session: InboxSessionItem,
  mcpIntegrationMap: Map<string, McpIntegrationMeta>
): SessionToolIcon[] {
  const icons = new Map<string, SessionToolIcon>()
  for (const tool of session.tools ?? []) {
    const namespace = reconstructActionType(tool)
      .split(".")
      .slice(0, 2)
      .join(".")
    if (!icons.has(namespace)) {
      icons.set(namespace, {
        id: namespace,
        label: namespace,
        iconKey: namespace,
      })
    }
  }
  for (const integrationId of session.mcpIntegrations ?? []) {
    const meta = mcpIntegrationMap.get(integrationId)
    icons.set(`mcp:${integrationId}`, {
      id: `mcp:${integrationId}`,
      label: meta?.name ?? "MCP server",
      providerId: getMcpProviderIconId(meta?.slug) ?? "custom",
    })
  }
  return [...icons.values()]
}

function getSessionTitle(session: InboxSessionItem): string {
  return (
    session.parent_workflow?.alias ||
    session.parent_workflow?.title ||
    session.title
  )
}

function PanelMessage({ children }: { children: React.ReactNode }) {
  return (
    <p className="p-4 text-sm text-muted-foreground" role="status">
      {children}
    </p>
  )
}

interface CaseAgentSessionRowProps {
  session: InboxSessionItem
  selected: boolean
  expanded: boolean
  /** The selected session's transcript outline; empty for other rows. */
  outline: TranscriptOutlineEntry[]
  mcpIntegrationMap: Map<string, McpIntegrationMeta>
  onSelect: (sessionId: string) => void
  onToggle: (sessionId: string) => void
  onJump: (outlineId: string) => void
}

/**
 * One session in the list. Memoized so the list's background refresh and a
 * selection change re-render only the rows whose props changed.
 */
const CaseAgentSessionRow = memo(function CaseAgentSessionRow({
  session,
  selected,
  expanded,
  outline,
  mcpIntegrationMap,
  onSelect,
  onToggle,
  onJump,
}: CaseAgentSessionRowProps) {
  const isLive = isLiveAgentStatus(session.derivedStatus)
  const toolIcons = getSessionToolIcons(session, mcpIntegrationMap)
  const hiddenTools = toolIcons.slice(MAX_TOOL_ICONS)
  const outlineId = `case-session-outline-${session.id}`

  return (
    <li>
      <div
        className={cn(
          "flex h-9 min-w-0 items-center rounded-md transition-colors hover:bg-muted/60",
          selected && "bg-muted"
        )}
      >
        <button
          type="button"
          aria-label={expanded ? "Collapse session" : "Expand session"}
          aria-expanded={expanded}
          aria-controls={outlineId}
          onClick={() => onToggle(session.id)}
          className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <ChevronRightIcon
            className={cn(
              "size-3.5 transition-transform",
              expanded && "rotate-90"
            )}
          />
        </button>
        <button
          type="button"
          aria-current={selected ? "true" : undefined}
          onClick={() => onSelect(session.id)}
          className="flex h-full min-w-0 flex-1 items-center gap-2 rounded-md pr-2 text-left focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-sm",
              selected && "font-medium"
            )}
          >
            {getSessionTitle(session)}
          </span>
          {/* Fixed-width slots, so titles, icons and times line up down the
              list whatever each row holds. */}
          <span className="flex w-3.5 shrink-0 justify-center">
            {/* Only states that need attention get a status mark. */}
            {isLive ? (
              <LoaderCircleIcon
                aria-label={session.statusLabel}
                className="size-3.5 animate-spin text-muted-foreground"
              />
            ) : session.statusTone === "danger" ? (
              <XCircleIcon
                aria-label={session.statusLabel}
                className="size-3.5 text-red-600"
              />
            ) : session.pendingApprovalCount > 0 ? (
              <CirclePauseIcon
                aria-label={`${session.pendingApprovalCount} pending approvals`}
                className="size-3.5 text-primary"
              />
            ) : null}
          </span>
          {/* Dropped on a list too narrow to keep the title readable. */}
          <span className="flex w-[4.5rem] shrink-0 items-center justify-end gap-1 [@container(max-width:16rem)]:hidden">
            {toolIcons.slice(0, MAX_TOOL_ICONS).map((tool) => (
              <Tooltip key={tool.id}>
                <TooltipTrigger asChild>
                  <span role="img" aria-label={tool.label} className="flex">
                    {tool.iconKey ? (
                      getIcon(tool.iconKey, {
                        className: "size-5 shrink-0 rounded border",
                        inline: true,
                      })
                    ) : (
                      <ProviderIcon
                        providerId={tool.providerId ?? "custom"}
                        className="size-5 shrink-0 rounded border"
                      />
                    )}
                  </span>
                </TooltipTrigger>
                <TooltipContent>{tool.label}</TooltipContent>
              </Tooltip>
            ))}
            {hiddenTools.length > 0 && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="w-5 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
                    +{hiddenTools.length}
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  <OverflowTooltipList
                    items={hiddenTools.map((tool) => ({
                      key: tool.id,
                      label: tool.label,
                    }))}
                  />
                </TooltipContent>
              </Tooltip>
            )}
          </span>
          <span className="flex w-11 shrink-0 justify-start">
            <UpdatedAtTimestamp updatedAt={session.updated_at} />
          </span>
        </button>
      </div>
      {expanded && (
        <ul
          id={outlineId}
          className="ml-3.5 flex flex-col gap-px border-l py-1 pl-2"
        >
          {outline.length === 0 ? (
            <li className="px-2 py-1 text-xs text-muted-foreground">
              No messages yet
            </li>
          ) : (
            outline.map((entry) => {
              const EntryIcon =
                entry.kind === "prompt" ? MessageSquareIcon : SparklesIcon
              return (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => onJump(entry.id)}
                    className="flex h-7 w-full min-w-0 items-center gap-2 rounded-md px-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <EntryIcon className="size-3 shrink-0" />
                    <span className="min-w-0 flex-1 truncate">
                      {entry.label}
                    </span>
                    {entry.toolCallCount > 0 && (
                      <span className="shrink-0 tabular-nums">
                        {entry.toolCallCount}{" "}
                        {entry.toolCallCount === 1 ? "tool" : "tools"}
                      </span>
                    )}
                  </button>
                </li>
              )
            })
          )}
        </ul>
      )}
    </li>
  )
})

const EMPTY_OUTLINE: TranscriptOutlineEntry[] = []
const EMPTY_MCP_MAP = new Map<string, McpIntegrationMeta>()

/**
 * The case's Agent sessions panel: every agent session associated with the
 * case in a list, newest activity first, beside the selected transcript. The
 * selected session expands into an outline that jumps to parts of it.
 * Fills its parent, which must give it a height.
 */
export function CaseAgentSessionsPanel({
  caseId,
  workspaceId,
  embedded = false,
}: CaseAgentSessionsPanelProps) {
  const canReadInbox = useScopeCheck("inbox:read")
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const entitled = hasEntitlement("case_addons")
  const enabled = canReadInbox === true && entitled

  const { sessions, isLoading, error, hasMore, isLoadingMore, loadMore } =
    useCaseAgentSessions({ caseId, workspaceId, enabled })

  // MCP server names and icons are only needed when a session has servers.
  const hasMcpIntegrations = sessions.some(
    (session) => (session.mcpIntegrations?.length ?? 0) > 0
  )
  // Reads the shared MCP integrations cache directly: the list hook also
  // starts verification polling, which this read-only view does not need.
  const { data: mcpIntegrations } = useQuery<MCPIntegrationRead[]>({
    queryKey: ["mcp-integrations", workspaceId, undefined],
    queryFn: () => mcpIntegrationsListMcpIntegrations({ workspaceId }),
    enabled: hasMcpIntegrations && Boolean(workspaceId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    meta: { suppressErrorToast: true },
  })
  const mcpIntegrationMap = useMemo(
    () =>
      mcpIntegrations
        ? new Map(
            mcpIntegrations.map((integration) => [
              integration.id,
              { name: integration.name, slug: integration.slug },
            ])
          )
        : EMPTY_MCP_MAP,
    [mcpIntegrations]
  )

  // Only the selected transcript is mounted, so one chat stream is alive at
  // a time. Until the user picks one, the newest session is shown.
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedSession =
    sessions.find((session) => session.id === selectedId) ?? sessions[0]
  // The outline comes from the mounted transcript, so only the selected
  // session can be expanded. It starts open.
  const [outlineOpen, setOutlineOpen] = useState(true)
  const [outline, setOutline] = useState<{
    sessionId: string
    entries: TranscriptOutlineEntry[]
  } | null>(null)

  const selectedSessionId = selectedSession?.id
  const handleOutline = useCallback(
    (entries: TranscriptOutlineEntry[]) => {
      if (!selectedSessionId) {
        return
      }
      // A streaming turn reports on every chunk; keep the state identity
      // unless a jump target or its label changed.
      setOutline((prev) => {
        if (
          prev?.sessionId === selectedSessionId &&
          prev.entries.length === entries.length &&
          prev.entries.every(
            (entry, index) =>
              entry.id === entries[index].id &&
              entry.label === entries[index].label &&
              entry.toolCallCount === entries[index].toolCallCount
          )
        ) {
          return prev
        }
        return { sessionId: selectedSessionId, entries }
      })
    },
    [selectedSessionId]
  )

  const handleSelect = useCallback((sessionId: string) => {
    setSelectedId(sessionId)
    setOutlineOpen(true)
  }, [])
  const handleToggle = useCallback(
    (sessionId: string) => {
      if (sessionId === selectedSessionId) {
        setOutlineOpen((open) => !open)
      } else {
        setSelectedId(sessionId)
        setOutlineOpen(true)
      }
    },
    [selectedSessionId]
  )

  const transcriptRef = useRef<HTMLDivElement>(null)
  const handleJump = useCallback((outlineId: string) => {
    transcriptRef.current
      ?.querySelector(`[data-outline-id="${CSS.escape(outlineId)}"]`)
      ?.scrollIntoView({ block: "start" })
  }, [])

  // The outline updates while a turn streams. A stable element keeps those
  // updates from re-rendering the transcript they came from.
  const transcript = useMemo(
    () =>
      selectedSession ? (
        <TranscriptOutlineContext.Provider value={handleOutline}>
          <InboxChat session={selectedSession} />
        </TranscriptOutlineContext.Provider>
      ) : null,
    [handleOutline, selectedSession]
  )

  if (entitlementsLoading || canReadInbox === undefined || isLoading) {
    return (
      <div className="p-4" aria-busy="true">
        <Skeleton className="h-5 w-48" />
      </div>
    )
  }
  if (!entitled) {
    return (
      <PanelMessage>
        Agent sessions on cases are available on Enterprise.
      </PanelMessage>
    )
  }
  if (!canReadInbox) {
    return (
      <PanelMessage>
        Your role cannot view agent sessions in this workspace.
      </PanelMessage>
    )
  }
  if (error) {
    return <PanelMessage>Agent sessions could not be loaded.</PanelMessage>
  }
  if (!selectedSession) {
    return <PanelMessage>No agent sessions for this case yet.</PanelMessage>
  }

  const selectedOutline =
    outline?.sessionId === selectedSession.id ? outline.entries : EMPTY_OUTLINE

  const sessionList = (
    <>
      <ul className="flex flex-col gap-0.5">
        {sessions.map((session) => {
          const selected = session.id === selectedSession.id
          return (
            <CaseAgentSessionRow
              key={session.id}
              session={session}
              selected={selected}
              expanded={selected && outlineOpen}
              outline={selected ? selectedOutline : EMPTY_OUTLINE}
              mcpIntegrationMap={mcpIntegrationMap}
              onSelect={handleSelect}
              onToggle={handleToggle}
              onJump={handleJump}
            />
          )
        })}
      </ul>
      {hasMore && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-1 h-7 text-xs text-muted-foreground"
          onClick={loadMore}
          disabled={isLoadingMore}
        >
          {isLoadingMore && <Spinner className="mr-1.5 size-3" />}
          Show more
        </Button>
      )}
    </>
  )

  // The embedded case panel is too narrow for two columns.
  if (embedded) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <nav
          aria-label="Agent sessions"
          className="no-scrollbar @container max-h-40 shrink-0 overflow-y-auto border-b p-2"
        >
          {sessionList}
        </nav>
        <div ref={transcriptRef} className="min-h-0 min-w-0 flex-1">
          {transcript}
        </div>
      </div>
    )
  }

  return (
    <ResizablePanelGroup
      direction="horizontal"
      autoSaveId="case-agent-sessions"
      className="h-full min-h-0"
    >
      <ResizablePanel
        defaultSize={32}
        minSize={18}
        maxSize={50}
        className="min-w-56"
      >
        <nav
          aria-label="Agent sessions"
          className="no-scrollbar @container h-full overflow-y-auto p-2"
        >
          {sessionList}
        </nav>
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel defaultSize={68} minSize={50}>
        <div ref={transcriptRef} className="h-full min-h-0 min-w-0">
          {transcript}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
