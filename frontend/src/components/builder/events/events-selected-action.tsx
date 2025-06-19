"use client"

import "@blocknote/core/fonts/inter.css"
import "@blocknote/shadcn/style.css"
import "@/components/cases/editor.css"

import React, { useEffect } from "react"
import {
  AgentOutput,
  EventFailure,
  InteractionRead,
  ModelRequest,
  ModelResponse,
  RetryPromptPart,
  SystemPromptPart,
  TextPart,
  ToolCallPart,
  ToolReturnPart,
  UserPromptPart,
} from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkflowBuilder } from "@/providers/builder"
import { codeBlock } from "@blocknote/code-block"
import { BlockNoteEditor } from "@blocknote/core"
import { useCreateBlockNote } from "@blocknote/react"
import { BlockNoteView } from "@blocknote/shadcn"
import {
  ChevronRightIcon,
  CircleDot,
  FileIcon,
  FilePlusIcon,
  FilterIcon,
  FunctionSquareIcon,
  ListIcon,
  LoaderIcon,
  MessageCircle,
  RefreshCw,
  SearchIcon,
  Undo2Icon,
} from "lucide-react"

import { SYSTEM_USER } from "@/lib/auth"
import {
  groupEventsByActionRef,
  isAgentOutput,
  parseStreamId,
  WorkflowExecutionEventCompact,
  WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import { useGetRegistryAction } from "@/lib/hooks"
import { getSpacedBlocks } from "@/lib/rich-text-editor"
import { cn, reconstructActionType } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getWorkflowEventIcon } from "@/components/builder/events/events-workflow"
import { CaseUserAvatar } from "@/components/cases/case-panel-common"
import { CodeBlock } from "@/components/code-block"
import { getIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { AlertNotification } from "@/components/notifications"
import { InlineDotSeparator } from "@/components/separator"

type TabType = "input" | "result" | "interaction"

// Add default agent tool icon mappings
const DEFAULT_AGENT_TOOLS: Record<string, JSX.Element> = {
  read_file: <FileIcon className="size-4" />,
  create_file: <FilePlusIcon className="size-4" />,
  search_files: <SearchIcon className="size-4" />,
  list_directory: <ListIcon className="size-4" />,
  grep_search: <SearchIcon className="size-4" />,
  find_and_replace: <RefreshCw className="size-4" />,
  jsonpath_find: <FilterIcon className="size-4" />,
  jsonpath_find_and_replace: <FilterIcon className="size-4" />,
  apply_python_lambda: <FunctionSquareIcon className="size-4" />,
}

const DEFAULT_TOOL_NAMES = new Set(Object.keys(DEFAULT_AGENT_TOOLS))

export function ActionEvent({
  execution,
  type,
}: {
  execution: WorkflowExecutionReadCompact
  type: TabType
}) {
  const { workflowId, selectedActionEventRef, setSelectedActionEventRef } =
    useWorkflowBuilder()

  if (!workflowId)
    return <AlertNotification level="error" message="No workflow in context" />

  let events = execution.events
  if (type === "interaction") {
    // Filter events to only include interaction events
    const interactionEvents = new Set(
      execution.interactions?.map((s) => s.action_ref) ?? []
    )
    events = events.filter((e) => interactionEvents.has(e.action_ref))
  }
  const groupedEvents = groupEventsByActionRef(events)
  return (
    <div className="flex flex-col gap-4 p-4">
      <Select
        value={selectedActionEventRef}
        onValueChange={setSelectedActionEventRef}
      >
        <SelectTrigger className="h-8 text-xs text-foreground/70 focus:ring-0 focus:ring-offset-0">
          <SelectValue placeholder="Select an event" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {Object.entries(groupedEvents).map(([actionRef, relatedEvents]) => (
              <SelectItem
                key={actionRef}
                value={actionRef}
                className="max-h-8 py-1 text-xs"
              >
                {actionRef}
                {relatedEvents.length !== 1 && ` (${relatedEvents.length})`}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <ActionEventView
        selectedRef={selectedActionEventRef}
        execution={execution}
        type={type}
      />
    </div>
  )
}

function ActionEventView({
  selectedRef,
  execution,
  type,
}: {
  selectedRef?: string
  execution: WorkflowExecutionReadCompact
  type: TabType
}) {
  const noEvent = (
    <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
      <CircleDot className="size-3 text-muted-foreground" />
      <span>Please select an event</span>
    </div>
  )
  if (!selectedRef) {
    return noEvent
  }
  if (type === "interaction") {
    const interaction = execution.interactions?.find(
      (s) => s.action_ref === selectedRef
    )
    if (!interaction) {
      // We reach this if we switch tabs or select an event that has no interaction state
      return noEvent
    }
    return (
      <ActionInteractionEventDetails
        eventRef={selectedRef}
        interaction={interaction}
      />
    )
  }
  return (
    <ActionEventDetails
      eventRef={selectedRef}
      status={execution.status}
      events={execution.events}
      type={type}
    />
  )
}

function ActionInteractionEventDetails({
  eventRef,
  interaction,
}: {
  eventRef: string
  interaction: InteractionRead
}) {
  if (interaction.response_payload === null) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        <CircleDot className="size-3 text-muted-foreground" />
        <span>No interaction data</span>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <JsonViewWithControls
        src={interaction.response_payload}
        defaultExpanded={true}
        copyPrefix={`ACTIONS.${eventRef}.interaction`}
      />
    </div>
  )
}

export function SuccessEvent({
  event,
  type,
  eventRef,
}: {
  event: WorkflowExecutionEventCompact
  type: Omit<TabType, "interaction">
  eventRef: string
}) {
  if (type === "result" && isAgentOutput(event.action_result)) {
    return <AgentOutputEvent agentOutput={event.action_result} />
  }
  return (
    <JsonViewWithControls
      src={type === "input" ? event.action_input : event.action_result}
      defaultExpanded={true}
      copyPrefix={`ACTIONS.${eventRef}.result`}
    />
  )
}

export function AgentOutputEvent({
  agentOutput,
}: {
  agentOutput: AgentOutput
}) {
  return (
    <div className="mb-16 mt-4 space-y-4">
      <div className="space-y-4">
        {agentOutput.message_history.map((m, index) => (
          <div key={index}>
            {m.kind === "response" && <AgentResponsePart parts={m.parts} />}
            {m.kind === "request" && <AgentRequestPart parts={m.parts} />}
          </div>
        ))}
      </div>
    </div>
  )
}

export function SystemPromptPartComponent({
  part,
  defaultExpanded = false,
}: {
  part: SystemPromptPart
  defaultExpanded?: boolean
}) {
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)
  const content =
    typeof part.content === "string"
      ? part.content
      : JSON.stringify(part.content, null, 2)

  const TRUNCATE_LIMIT = 200
  const shouldTruncate = content.length > TRUNCATE_LIMIT

  // For collapsed view: normalize whitespace and truncate
  const normalizedContent = content.replace(/\s+/g, " ").trim()
  const displayContent = isExpanded
    ? content
    : shouldTruncate
      ? normalizedContent.substring(0, TRUNCATE_LIMIT) + "..."
      : normalizedContent

  return (
    <Card
      className="cursor-pointer rounded-lg border-[0.5px] bg-muted/40 p-3 text-xs leading-normal shadow-sm hover:bg-muted/50"
      onClick={() => setIsExpanded(!isExpanded)}
    >
      <div className="flex items-start gap-2">
        <CaseUserAvatar user={SYSTEM_USER} size="sm" />
        <div
          className={`flex-1 overflow-x-auto break-words ${isExpanded ? "whitespace-pre-wrap" : "whitespace-nowrap"}`}
        >
          {displayContent}
        </div>
        {shouldTruncate && (
          <ChevronRightIcon
            className={`ml-2 size-4 shrink-0 transition-transform ${isExpanded ? "rotate-90" : ""}`}
          />
        )}
      </div>
    </Card>
  )
}

export function UserPromptPartComponent({
  part,
  defaultExpanded = false,
}: {
  part: UserPromptPart
  defaultExpanded?: boolean
}) {
  const { user } = useAuth()
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)
  const content =
    typeof part.content === "string"
      ? part.content
      : JSON.stringify(part.content, null, 2)

  const TRUNCATE_LIMIT = 200
  const shouldTruncate = content.length > TRUNCATE_LIMIT

  // For collapsed view: normalize whitespace and truncate
  const normalizedContent = content.replace(/\s+/g, " ").trim()
  const displayContent = isExpanded
    ? content
    : shouldTruncate
      ? normalizedContent.substring(0, TRUNCATE_LIMIT) + "..."
      : normalizedContent

  return (
    <Card
      className="cursor-pointer rounded-lg border-[0.5px] bg-muted/40 p-3 text-xs leading-normal shadow-sm hover:bg-muted/50"
      onClick={() => setIsExpanded(!isExpanded)}
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {user && <CaseUserAvatar user={user} size="sm" />}
            {user && (
              <span className="text-xs font-semibold text-foreground/80">
                {user.firstName || user.email}
              </span>
            )}
          </div>
          {shouldTruncate && (
            <ChevronRightIcon
              className={`size-4 shrink-0 transition-transform ${isExpanded ? "rotate-90" : ""}`}
            />
          )}
        </div>
        <div
          className={`overflow-x-auto break-words ${isExpanded ? "whitespace-pre-wrap" : "whitespace-nowrap"}`}
        >
          {displayContent}
        </div>
      </div>
    </Card>
  )
}

export function RetryPromptPartComponent({
  part,
  defaultExpanded = false,
}: {
  part: RetryPromptPart
  defaultExpanded?: boolean
}) {
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)
  const content =
    typeof part.content === "string"
      ? part.content
      : part.content.map((c) => c.msg).join(" ")

  const TRUNCATE_LIMIT = 200
  const shouldTruncate = content.length > TRUNCATE_LIMIT

  // For collapsed view: normalize whitespace and truncate
  const normalizedContent = content.replace(/\s+/g, " ").trim()
  const displayContent = isExpanded
    ? typeof part.content === "string"
      ? part.content
      : part.content.map((c) => {
          return <span key={c.msg}>{c.msg}</span>
        })
    : shouldTruncate
      ? normalizedContent.substring(0, TRUNCATE_LIMIT) + "..."
      : normalizedContent

  return (
    <Card
      className="flex cursor-pointer flex-col gap-2 rounded-lg border-[0.5px] bg-muted/40 p-2 text-xs leading-normal shadow-sm hover:bg-muted/50"
      onClick={() => setIsExpanded(!isExpanded)}
    >
      <div className="flex items-center justify-between gap-1">
        <div className="flex items-center gap-1">
          <RefreshCw className="size-3 text-muted-foreground" />
          <span className="text-xs font-semibold text-foreground/80">
            Retry prompt
          </span>
        </div>
        {shouldTruncate && (
          <ChevronRightIcon
            className={`size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
          />
        )}
      </div>
      <div className={`${isExpanded ? "whitespace-pre-wrap" : "truncate"}`}>
        {displayContent}
      </div>
    </Card>
  )
}

export function ToolReturnPartComponent({ part }: { part: ToolReturnPart }) {
  const [isExpanded, setIsExpanded] = React.useState(false)

  const toolName = part.tool_name
  const isDefaultTool = DEFAULT_TOOL_NAMES.has(toolName)

  // Always resolve action type so hooks are called consistently
  const actionType = reconstructActionType(toolName)
  // Call hook unconditionally; it will be disabled internally when actionType is undefined
  const { registryAction, registryActionIsLoading, registryActionError } =
    useGetRegistryAction(isDefaultTool ? undefined : actionType)

  // Case 1 – default agent tool
  if (isDefaultTool) {
    const iconElement = DEFAULT_AGENT_TOOLS[toolName]
    return (
      <div className="flex flex-col gap-2">
        <Card
          className="flex cursor-pointer flex-col gap-1 rounded-md border-[0.5px] bg-muted/20 text-xs shadow-sm hover:bg-muted/40"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          <div
            className={cn(
              "flex items-center gap-2 p-2",
              isExpanded && "border-b-[0.5px]"
            )}
          >
            <div className="rounded-sm border-[0.5px] p-[3px]">
              {iconElement}
            </div>
            <span className="text-xs font-semibold text-foreground/80">
              {toolName}
            </span>
            <Undo2Icon className="size-3" />
            <ChevronRightIcon
              className={`ml-auto size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
            />
          </div>
        </Card>
        {isExpanded && (
          <JsonViewWithControls
            src={part.content}
            defaultExpanded={true}
            defaultTab="nested"
            className="shadow-sm"
          />
        )}
      </div>
    )
  }

  // Case 2 – registry action
  if (registryActionIsLoading) {
    return <Skeleton className="h-16 w-full" />
  }
  if (registryAction && !registryActionError) {
    return (
      <div className="flex flex-col gap-2">
        <Card
          className="flex cursor-pointer flex-col gap-1 rounded-md border-[0.5px] bg-muted/20 text-xs shadow-sm hover:bg-muted/40"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          <div
            className={cn(
              "flex items-center gap-2 p-2",
              isExpanded && "border-b-[0.5px]"
            )}
          >
            <Tooltip>
              <TooltipTrigger>
                <div>
                  {getIcon(actionType, {
                    className: "size-4 p-[3px] border-[0.5px]",
                  })}
                </div>
              </TooltipTrigger>
              <TooltipContent className="p-1">
                <p>{registryAction.namespace}</p>
              </TooltipContent>
            </Tooltip>
            <span className="text-xs font-semibold text-foreground/80">
              {registryAction.default_title}
            </span>
            <Undo2Icon className="size-3" />
            <ChevronRightIcon
              className={`ml-auto size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
            />
          </div>
        </Card>
        {isExpanded && (
          <JsonViewWithControls
            src={part.content}
            defaultExpanded={true}
            defaultTab="nested"
            className="shadow-sm"
          />
        )}
      </div>
    )
  }

  // Case 3 – not found
  return (
    <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
      <CircleDot className="size-3 text-muted-foreground" />
      <span>Action not found</span>
    </div>
  )
}

export function AgentRequestPart({ parts }: { parts: ModelRequest["parts"] }) {
  return (
    <div className="space-y-2">
      {parts.map((p, index) => (
        <div key={index} className="space-y-2 text-xs">
          {p.part_kind === "system-prompt" && (
            <SystemPromptPartComponent part={p} />
          )}
          {p.part_kind === "user-prompt" && (
            <UserPromptPartComponent part={p} />
          )}
          {p.part_kind === "tool-return" && (
            <ToolReturnPartComponent part={p} />
          )}
          {p.part_kind === "retry-prompt" && (
            <RetryPromptPartComponent part={p} />
          )}
        </div>
      ))}
    </div>
  )
}

export function AgentResponsePart({
  parts,
}: {
  parts: ModelResponse["parts"]
}) {
  return (
    <div className="space-y-2">
      {parts.map((p, index) => (
        <div key={index} className="space-y-2 text-xs">
          {p.part_kind === "text" && <TextPartComponent text={p} />}
          {p.part_kind === "tool-call" && (
            <ToolCallPartComponent toolCall={p} />
          )}
        </div>
      ))}
    </div>
  )
}

export function TextPartComponent({
  text,
  defaultExpanded = false,
}: {
  text: TextPart
  defaultExpanded?: boolean
}) {
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)
  const editor = useCreateBlockNote({
    animations: false,
    codeBlock,
  })

  const TRUNCATE_LIMIT = 200
  const shouldTruncate = text.content.length > TRUNCATE_LIMIT
  useEffect(() => {
    if (text.content) {
      let contentToLoad
      if (isExpanded) {
        contentToLoad = text.content
      } else {
        // For collapsed view: normalize whitespace and truncate
        const normalizedContent = text.content.replace(/\s+/g, " ").trim()
        contentToLoad = shouldTruncate
          ? normalizedContent.substring(0, TRUNCATE_LIMIT) + "..."
          : normalizedContent
      }

      loadInitialContent(editor, contentToLoad)
    }
  }, [text.content, editor, isExpanded, shouldTruncate])

  return (
    <div
      className="flex cursor-pointer flex-col gap-2 overflow-scroll whitespace-pre-wrap rounded-md border-[0.5px] bg-muted/20 p-3 text-xs shadow-sm hover:bg-muted/30"
      onClick={() => setIsExpanded(!isExpanded)}
    >
      <div className="flex items-center justify-between gap-1">
        <div className="flex items-center gap-1">
          <MessageCircle className="size-4" />
          <span className="text-xs font-semibold text-foreground/80">
            Agent
          </span>
        </div>
        {shouldTruncate && (
          <ChevronRightIcon
            className={`size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
          />
        )}
      </div>

      <BlockNoteView
        editor={editor}
        theme="light"
        editable={false}
        slashMenu={false}
        style={{
          height: "100%",
          width: "100%",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          lineHeight: "1.5",
        }}
      />
    </div>
  )
}

export function ToolCallPartComponent({
  toolCall,
  defaultExpanded = true,
}: {
  toolCall: ToolCallPart
  defaultExpanded?: boolean
}) {
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)

  const toolName = toolCall.tool_name
  const isDefaultTool = DEFAULT_TOOL_NAMES.has(toolName)

  // Always resolve action type so hooks are called consistently
  const actionType = reconstructActionType(toolName)
  const { registryAction, registryActionIsLoading, registryActionError } =
    useGetRegistryAction(isDefaultTool ? undefined : actionType)

  let args
  try {
    args =
      typeof toolCall.args === "string"
        ? JSON.parse(toolCall.args)
        : toolCall.args
  } catch {
    args = toolCall.args
  }

  // Case 1 – default agent tool
  if (isDefaultTool) {
    const iconElement = DEFAULT_AGENT_TOOLS[toolName]
    return (
      <Card
        className="cursor-pointer rounded-md border-[0.5px] bg-muted/20 p-2 text-xs shadow-sm"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-2">
          <div className="rounded-sm border-[0.5px] p-[3px]">{iconElement}</div>
          <span className="text-xs font-semibold text-foreground/80">
            {toolName}
          </span>
          <ChevronRightIcon
            className={`ml-auto size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
          />
        </div>
        {isExpanded && (
          <table className="mt-2 min-w-full text-xs">
            <tbody>
              {Object.entries(args).map(([key, value]) => (
                <tr key={key}>
                  <td className="px-2 py-1 text-left align-top font-semibold text-foreground/80">
                    {key}
                  </td>
                  <td className="px-2 py-1 text-left align-top text-foreground/90">
                    {typeof value === "string"
                      ? value
                      : JSON.stringify(value, null, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    )
  }

  // Case 2 – registry action
  if (registryActionIsLoading) {
    return (
      <Card className="rounded-md border-[0.5px] bg-muted/20 p-2 text-xs shadow-sm">
        <div className="flex items-center gap-2">
          <Skeleton className="size-4 border-[0.5px] p-[3px]" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="ml-auto size-4 animate-pulse" />
        </div>
        {isExpanded && (
          <div className="mt-2">
            <Skeleton className="h-16 w-full" />
          </div>
        )}
      </Card>
    )
  }
  if (registryAction && !registryActionError) {
    return (
      <Card
        className="cursor-pointer rounded-md border-[0.5px] bg-muted/20 p-2 text-xs shadow-sm"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-2">
          <Tooltip>
            <TooltipTrigger>
              <div>
                {getIcon(actionType, {
                  className: "size-4 p-[3px] border-[0.5px]",
                })}
              </div>
            </TooltipTrigger>
            <TooltipContent className="p-1">
              <p>{registryAction.namespace}</p>
            </TooltipContent>
          </Tooltip>
          <span className="text-xs font-semibold text-foreground/80">
            {registryAction.default_title}
          </span>
          <ChevronRightIcon
            className={`ml-auto size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
          />
        </div>
        {isExpanded && (
          <table className="mt-2 min-w-full text-xs">
            <tbody>
              {Object.entries(args).map(([key, value]) => (
                <tr key={key}>
                  <td className="px-2 py-1 text-left align-top font-semibold text-foreground/80">
                    {key}
                  </td>
                  <td className="px-2 py-1 text-left align-top text-foreground/90">
                    {typeof value === "string"
                      ? value
                      : JSON.stringify(value, null, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    )
  }

  // Case 3 – action not found
  return (
    <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
      <CircleDot className="size-3 text-muted-foreground" />
      <span>Action not found</span>
    </div>
  )
}

export function ActionEventDetails({
  eventRef,
  status,
  events,
  type,
}: {
  eventRef: string
  status: WorkflowExecutionReadCompact["status"]
  events: WorkflowExecutionEventCompact[]
  type: Omit<TabType, "interaction">
}) {
  const actionEventsForRef = events.filter((e) => e.action_ref === eventRef)
  // No events for ref, either the action has not executed or there was no event for the action.
  if (actionEventsForRef.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        {status === "RUNNING" ? (
          <>
            <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
            <span>Waiting for events...</span>
          </>
        ) : (
          <>
            <CircleDot className="size-3 text-muted-foreground" />
            <span>No events</span>
          </>
        )}
      </div>
    )
  }
  const renderEvent = (
    actionEvent: WorkflowExecutionEventCompact,
    streamIdPlaceholder?: string
  ) => {
    if (["SCHEDULED", "STARTED"].includes(actionEvent.status)) {
      return (
        <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
          <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
          <span>Action is {actionEvent.status.toLowerCase()}...</span>
        </div>
      )
    }
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <Badge variant="secondary" className="items-center gap-2">
            {getWorkflowEventIcon(actionEvent.status, "size-4")}
            <span className="text-xs font-semibold text-foreground/70">
              Action {actionEvent.status.toLowerCase()}
            </span>
          </Badge>
          {actionEvent.stream_id && !streamIdPlaceholder && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground/80">
              {parseStreamId(actionEvent.stream_id)
                .filter((part) => part.scope !== "<root>")
                // Only sort if scope matches, otherwise preserve original order
                .sort((a, b) => {
                  if (a.scope === b.scope) {
                    return Number(a.index) - Number(b.index)
                  }
                  // If scopes do not match, preserve original order (no sorting)
                  return 0
                })
                // Insert a ">" separator between mapped elements, but not after the last one
                .map((part, idx, arr) => (
                  <div key={part.scope} className="flex items-center gap-1">
                    <span className="flex items-center gap-1">
                      <span>{part.scope}</span>
                      <InlineDotSeparator />
                      <span>{part.index}</span>
                    </span>
                    {idx < arr.length - 1 && (
                      <ChevronRightIcon className="size-3" />
                    )}
                  </div>
                ))}
            </div>
          )}
          {streamIdPlaceholder && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground/80">
              <span>{streamIdPlaceholder}</span>
            </div>
          )}
        </div>

        {type === "result" && actionEvent.action_error ? (
          <ErrorEvent failure={actionEvent.action_error} />
        ) : (
          <SuccessEvent event={actionEvent} type={type} eventRef={eventRef} />
        )}
      </div>
    )
  }
  if (type === "input") {
    // Inputs are identical for all events, so we can just render the first one
    return renderEvent(
      actionEventsForRef[0],
      "Input is the same for all events"
    )
  }
  return actionEventsForRef.map((actionEvent) => (
    <div key={actionEvent.stream_id}>{renderEvent(actionEvent)}</div>
  ))
}

function ErrorEvent({ failure }: { failure: EventFailure }) {
  return (
    <div className="flex flex-col space-y-8 text-xs">
      <CodeBlock title="Error Message">{failure.message}</CodeBlock>
    </div>
  )
}

async function loadInitialContent(editor: BlockNoteEditor, content: string) {
  const blocks = await editor.tryParseMarkdownToBlocks(content)
  const spacedBlocks = getSpacedBlocks(blocks)
  editor.replaceBlocks(editor.document, spacedBlocks)
}
