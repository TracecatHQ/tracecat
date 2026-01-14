import type { Node, NodeProps } from "@xyflow/react"
import type { LucideIcon } from "lucide-react"
import {
  CalendarCheck,
  CalendarClockIcon,
  CheckSquare,
  CircleCheck,
  Eye,
  FilePlus2,
  Flag,
  Flame,
  FolderInput,
  GitCompare,
  Paperclip,
  PenSquare,
  RotateCcw,
  Shield,
  ShieldOff,
  SquareStack,
  Tag,
  TimerOffIcon,
  UnplugIcon,
  UserRound,
  WebhookIcon,
  Zap,
} from "lucide-react"
import React, { useMemo } from "react"
import type { CaseEventType, WorkflowRead } from "@/client"
import { TriggerSourceHandle } from "@/components/builder/canvas/custom-handle"
import { nodeStyles } from "@/components/builder/canvas/node-styles"
import { getIcon } from "@/components/icons"
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useTriggerNodeZoomBreakpoint } from "@/hooks/canvas"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useSchedules } from "@/lib/hooks"
import { durationToHumanReadable } from "@/lib/time"
import { cn } from "@/lib/utils"
import { useWorkflow } from "@/providers/workflow"

export type TriggerNodeData = {
  title: string
}
export type TriggerNodeType = Node<TriggerNodeData, "trigger">
export const TriggerTypename = "trigger" as const

// Case trigger config stored in workflow.object.nodes[].data.caseTriggers
interface CaseTriggerConfig {
  id: string
  enabled: boolean
  eventType: CaseEventType
  fieldFilters: Record<string, unknown>
  allowSelfTrigger: boolean
}

type WorkflowObject = {
  nodes?: Array<{
    type?: string
    data?: { caseTriggers?: CaseTriggerConfig[] }
  }>
}

type WorkflowReadWithObject = WorkflowRead & { object?: WorkflowObject }

// Event type option with icon
interface CaseTriggerEventOption {
  value: CaseEventType
  label: string
  icon: LucideIcon
}

// Event types that can trigger workflows with friendly labels and icons
const CASE_TRIGGER_EVENT_OPTIONS: CaseTriggerEventOption[] = [
  { value: "case_created", label: "Case created", icon: FilePlus2 },
  { value: "case_updated", label: "Case updated", icon: PenSquare },
  { value: "case_closed", label: "Case closed", icon: CircleCheck },
  { value: "case_reopened", label: "Case reopened", icon: RotateCcw },
  { value: "case_viewed", label: "Case viewed", icon: Eye },
  { value: "priority_changed", label: "Priority changed", icon: Flag },
  { value: "severity_changed", label: "Severity changed", icon: Flame },
  { value: "status_changed", label: "Status changed", icon: GitCompare },
  { value: "fields_changed", label: "Fields changed", icon: PenSquare },
  { value: "assignee_changed", label: "Assignee changed", icon: UserRound },
  { value: "attachment_created", label: "Attachment created", icon: Paperclip },
  { value: "attachment_deleted", label: "Attachment deleted", icon: Paperclip },
  { value: "tag_added", label: "Tag added", icon: Tag },
  { value: "tag_removed", label: "Tag removed", icon: Tag },
  { value: "payload_changed", label: "Payload changed", icon: SquareStack },
  { value: "task_created", label: "Task created", icon: CheckSquare },
  { value: "task_deleted", label: "Task deleted", icon: CheckSquare },
  {
    value: "task_status_changed",
    label: "Task status changed",
    icon: CheckSquare,
  },
  {
    value: "task_priority_changed",
    label: "Task priority changed",
    icon: CheckSquare,
  },
  {
    value: "task_workflow_changed",
    label: "Task workflow changed",
    icon: CheckSquare,
  },
  {
    value: "task_assignee_changed",
    label: "Task assignee changed",
    icon: CheckSquare,
  },
]

// Helper to get event option by value
function getEventOption(value: CaseEventType): CaseTriggerEventOption {
  return (
    CASE_TRIGGER_EVENT_OPTIONS.find((opt) => opt.value === value) ?? {
      value,
      label: value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      icon: Zap,
    }
  )
}

export default React.memo(function TriggerNode({
  type,
  data: { title },
  selected,
}: NodeProps<TriggerNodeType>) {
  const { workflow } = useWorkflow()
  const { breakpoint, style } = useTriggerNodeZoomBreakpoint()
  if (!workflow) {
    return null
  }

  return (
    <Card
      className={cn(
        "w-64",
        nodeStyles.common,
        selected ? nodeStyles.selected : nodeStyles.hover
      )}
    >
      <CardHeader className="p-4">
        <div className="flex w-full items-center space-x-4">
          {getIcon(type, {
            className: "size-10 p-2",
          })}

          <div className="flex w-full flex-1 justify-between space-x-12">
            <div className="flex flex-col">
              <CardTitle className="flex w-full items-center justify-between text-xs font-medium leading-none">
                <div
                  className={cn(
                    style.fontSize,
                    breakpoint !== "large" && "w-full"
                  )}
                >
                  {title}
                </div>
              </CardTitle>
              {style.showContent && (
                <CardDescription className="mt-2 text-xs text-muted-foreground">
                  Workflow trigger
                </CardDescription>
              )}
            </div>
            <div className="flex items-start">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    {workflow.webhook.api_key?.is_active ? (
                      <Shield className="size-4 text-emerald-400" />
                    ) : workflow.webhook.api_key ? (
                      <Shield className="size-4 text-amber-400" />
                    ) : (
                      <ShieldOff className="size-4 text-muted-foreground/70" />
                    )}
                  </TooltipTrigger>
                  <TooltipContent side="top" sideOffset={4}>
                    {workflow.webhook.api_key?.is_active
                      ? "Webhook is protected"
                      : workflow.webhook.api_key
                        ? "API key revoked"
                        : "Webhook is unprotected"}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          </div>
        </div>
      </CardHeader>
      {style.showContent && (
        <>
          <Separator />
          <div className="p-4">
            <div className="space-y-4">
              {/* Webhook status */}
              <div
                className={cn(
                  "flex h-8 items-center justify-center gap-1 rounded-lg border text-xs text-muted-foreground",
                  workflow?.webhook.status === "offline"
                    ? "bg-muted-foreground/5 text-muted-foreground/50"
                    : "bg-background text-emerald-500"
                )}
              >
                <WebhookIcon className="size-3" />
                <span>Webhook</span>
                <span
                  className={cn(
                    "ml-2 inline-block size-2 rounded-full ",
                    workflow.webhook.status === "online"
                      ? "bg-emerald-500"
                      : "bg-gray-300"
                  )}
                />
              </div>
              {/* Schedule table */}
              <div className="rounded-lg border">
                <TriggerNodeSchedulesTable workflowId={workflow.id} />
              </div>
              {/* Case triggers table (enterprise only) */}
              <TriggerNodeCaseTriggersTable workflow={workflow} />
            </div>
          </div>
        </>
      )}
      <TriggerSourceHandle />
    </Card>
  )
})

function TriggerNodeSchedulesTable({ workflowId }: { workflowId: string }) {
  const { schedules, schedulesIsLoading, schedulesError } =
    useSchedules(workflowId)

  if (schedulesIsLoading) {
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="h-8 text-center text-xs" colSpan={2}>
              <div className="flex items-center justify-center gap-1">
                <Skeleton className="size-3" />
                <Skeleton className="h-3 w-16" />
              </div>
            </TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          <TableRow className="items-center text-center text-xs">
            <TableCell>
              <div className="flex w-full items-center justify-center gap-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="size-2 rounded-full" />
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    )
  }
  if (schedulesError || !schedules) {
    return <UnplugIcon className="size-4 text-muted-foreground" />
  }

  const now = new Date()

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="h-8 text-center text-xs" colSpan={2}>
            <div className="flex items-center justify-center gap-1">
              <CalendarCheck className="size-3" />
              <span>Schedules</span>
            </div>
          </TableHead>
        </TableRow>
      </TableHeader>

      <TableBody>
        {schedules.length > 0 ? (
          schedules.map(({ id, status, every, cron, start_at, end_at }) => {
            const isCron = Boolean(cron)
            const label = isCron
              ? cron
              : every
                ? `Every ${durationToHumanReadable(every)}`
                : "Scheduled"

            const start = start_at ? new Date(start_at) : null
            const end = end_at ? new Date(end_at) : null
            const hasStart = start && !Number.isNaN(start.getTime())
            const hasEnd = end && !Number.isNaN(end.getTime())

            const isUpcoming =
              status === "online" && hasStart && now < (start as Date)
            const isExpired =
              status === "online" && hasEnd && now > (end as Date)
            const hasWindow = hasStart || hasEnd

            return (
              <TableRow
                key={id}
                className="items-center text-center text-xs text-muted-foreground"
              >
                <TableCell>
                  <div className="flex w-full items-center justify-center gap-2">
                    <span
                      className={cn(
                        "flex items-center gap-2",
                        status === "offline" && "text-muted-foreground/80"
                      )}
                    >
                      {hasWindow && (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <CalendarClockIcon className="size-3 text-muted-foreground" />
                            </TooltipTrigger>
                            <TooltipContent side="top" sideOffset={4}>
                              {hasStart && hasEnd && start && end
                                ? `Active from ${start.toLocaleString()} to ${end.toLocaleString()}`
                                : hasStart && start
                                  ? `Starts at ${start.toLocaleString()}`
                                  : hasEnd && end
                                    ? `Ends at ${end.toLocaleString()}`
                                    : "Time window configured"}
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      )}
                      {isCron ? (
                        <>
                          <span className="font-medium">Cron</span>
                          <code className="rounded bg-muted px-1 py-0.5 text-[11px]">
                            {label}
                          </code>
                        </>
                      ) : (
                        label
                      )}
                    </span>
                    {status === "online" ? (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span
                              className={cn(
                                "inline-block size-2 rounded-full",
                                isUpcoming && "bg-amber-400",
                                isExpired && "bg-muted-foreground",
                                !isUpcoming && !isExpired && "bg-emerald-500"
                              )}
                            />
                          </TooltipTrigger>
                          <TooltipContent side="top" sideOffset={4}>
                            {isUpcoming && start
                              ? `Starts at ${start.toLocaleString()}`
                              : isExpired && end
                                ? `Ended at ${end.toLocaleString()}`
                                : "Schedule active"}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : (
                      <TimerOffIcon className="size-3 text-muted-foreground" />
                    )}
                  </div>
                </TableCell>
              </TableRow>
            )
          })
        ) : (
          <TableRow className="justify-center text-xs text-muted-foreground">
            <TableCell
              className="h-8 bg-muted-foreground/5 text-center"
              colSpan={4}
            >
              No schedules
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  )
}

function TriggerNodeCaseTriggersTable({
  workflow,
}: {
  workflow: WorkflowReadWithObject
}) {
  const { isFeatureEnabled, isLoading: featureFlagLoading } = useFeatureFlag()

  // Parse existing triggers from workflow.object
  const caseTriggers = useMemo(() => {
    if (!workflow.object) return []
    const nodes = workflow.object.nodes ?? []
    const triggerNode = nodes.find((n) => n.type === "trigger")
    return triggerNode?.data?.caseTriggers ?? []
  }, [workflow.object])

  // Don't render if feature flag is loading or not enabled
  if (featureFlagLoading || !isFeatureEnabled("case-triggers")) {
    return null
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="h-8 text-center text-xs" colSpan={2}>
              <div className="flex items-center justify-center gap-1">
                <FolderInput className="size-3" />
                <span>Case triggers</span>
              </div>
            </TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {caseTriggers.length > 0 ? (
            caseTriggers.map((trigger) => {
              const eventOption = getEventOption(trigger.eventType)
              const EventIcon = eventOption.icon

              return (
                <TableRow
                  key={trigger.id}
                  className="items-center text-center text-xs text-muted-foreground"
                >
                  <TableCell>
                    <div className="flex w-full items-center justify-center gap-2">
                      <span
                        className={cn(
                          "flex items-center gap-2",
                          !trigger.enabled && "text-muted-foreground/80"
                        )}
                      >
                        <EventIcon className="size-3" />
                        <span>{eventOption.label}</span>
                      </span>
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span
                              className={cn(
                                "inline-block size-2 rounded-full",
                                trigger.enabled
                                  ? "bg-emerald-500"
                                  : "bg-gray-300"
                              )}
                            />
                          </TooltipTrigger>
                          <TooltipContent side="top" sideOffset={4}>
                            {trigger.enabled
                              ? trigger.allowSelfTrigger
                                ? "Active (self-trigger enabled)"
                                : "Active"
                              : "Disabled"}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })
          ) : (
            <TableRow className="justify-center text-xs text-muted-foreground">
              <TableCell
                className="h-8 bg-muted-foreground/5 text-center"
                colSpan={4}
              >
                No case triggers
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}
