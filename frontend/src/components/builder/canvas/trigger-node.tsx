import type { Node, NodeProps } from "@xyflow/react"
import {
  CalendarCheck,
  CalendarClockIcon,
  Shield,
  ShieldOff,
  SquarePlay,
  TimerOffIcon,
  UnplugIcon,
  WebhookIcon,
} from "lucide-react"
import React, { useCallback, useLayoutEffect, useMemo, useRef } from "react"
import { TriggerSourceHandle } from "@/components/builder/canvas/custom-handle"
import { nodeStyles } from "@/components/builder/canvas/node-styles"
import {
  DEFAULT_TRIGGER_PANEL_TAB,
  type TriggerPanelTab,
  TriggerPanelTabs,
} from "@/components/builder/panel/trigger-panel-tabs"
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
import { useCaseTrigger, useSchedules } from "@/lib/hooks"
import { durationToHumanReadable } from "@/lib/time"
import { cn } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"

export type TriggerNodeData = {
  title: string
}
export type TriggerNodeType = Node<TriggerNodeData, "trigger">
export const TriggerTypename = "trigger" as const

export default React.memo(function TriggerNode({
  id,
  type,
  data: { title },
  selected,
}: NodeProps<TriggerNodeType>) {
  const { workflow } = useWorkflow()
  const {
    reactFlow,
    workspaceId,
    workflowId,
    actionPanelRef,
    setTriggerPanelTab,
  } = useWorkflowBuilder()
  const { breakpoint, style } = useTriggerNodeZoomBreakpoint()
  const { isFeatureEnabled } = useFeatureFlag()
  const {
    data: caseTrigger,
    isLoading: caseTriggerIsLoading,
    error: caseTriggerError,
  } = useCaseTrigger(workspaceId, workflowId)
  const caseEventTypes = caseTrigger?.event_types ?? []
  const tagFilters = caseTrigger?.tag_filters ?? []
  const isCaseTriggerEnabled = caseTrigger?.status === "online"
  const eventKey = useMemo(() => caseEventTypes.join("|"), [caseEventTypes])
  const hasCaseTriggerConfig =
    caseEventTypes.length > 0 || tagFilters.length > 0
  const cardRef = useRef<HTMLDivElement>(null)
  const previousHeightRef = useRef<number | null>(null)
  const pendingHeightAdjustmentRef = useRef(false)
  const lastEventKeyRef = useRef<string | null>(null)
  const openTriggerPanel = useCallback(
    (tab: TriggerPanelTab) => {
      setTriggerPanelTab(tab)
      if (actionPanelRef.current?.isCollapsed()) {
        actionPanelRef.current.expand()
      }
    },
    [actionPanelRef, setTriggerPanelTab]
  )
  const handleDefaultPanelOpen = useCallback(() => {
    openTriggerPanel(DEFAULT_TRIGGER_PANEL_TAB)
  }, [openTriggerPanel])

  const pushNodesDown = useCallback(
    (delta: number) => {
      if (delta <= 0) return
      reactFlow.setNodes((nodes) => {
        const triggerNode = nodes.find((node) => node.id === id)
        if (!triggerNode) {
          return nodes
        }
        const triggerY = triggerNode.position.y
        return nodes.map((node) => {
          if (node.id === id) {
            return node
          }
          if (node.position.y <= triggerY) {
            return node
          }
          return {
            ...node,
            position: {
              ...node.position,
              y: node.position.y + delta,
            },
          }
        })
      })
    },
    [id, reactFlow]
  )

  useLayoutEffect(() => {
    if (!cardRef.current) {
      return
    }
    if (
      eventKey === lastEventKeyRef.current &&
      previousHeightRef.current !== null
    ) {
      return
    }
    lastEventKeyRef.current = eventKey
    const currentHeight = cardRef.current.getBoundingClientRect().height

    if (previousHeightRef.current == null) {
      previousHeightRef.current = currentHeight
      return
    }

    if (!style.showContent) {
      pendingHeightAdjustmentRef.current = true
      return
    }

    const delta = currentHeight - previousHeightRef.current
    if (delta > 0) {
      pushNodesDown(delta)
      previousHeightRef.current = currentHeight
    }
    pendingHeightAdjustmentRef.current = false
  }, [eventKey, pushNodesDown, style.showContent])

  useLayoutEffect(() => {
    if (!style.showContent || !pendingHeightAdjustmentRef.current) {
      return
    }
    if (!cardRef.current) {
      return
    }
    const currentHeight = cardRef.current.getBoundingClientRect().height
    const previousHeight = previousHeightRef.current
    if (previousHeight == null) {
      previousHeightRef.current = currentHeight
      pendingHeightAdjustmentRef.current = false
      return
    }
    const delta = currentHeight - previousHeight
    if (delta > 0) {
      pushNodesDown(delta)
      previousHeightRef.current = currentHeight
    }
    pendingHeightAdjustmentRef.current = false
  }, [pushNodesDown, style.showContent])
  if (!workflow) {
    return null
  }

  return (
    <Card
      ref={cardRef}
      onClickCapture={handleDefaultPanelOpen}
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
                  "flex h-8 cursor-pointer items-center justify-center gap-1 rounded-lg border text-xs text-muted-foreground",
                  workflow?.webhook.status === "offline"
                    ? "bg-muted-foreground/5 text-muted-foreground/50"
                    : "bg-background text-emerald-500"
                )}
                onClick={() => openTriggerPanel(TriggerPanelTabs.webhook)}
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
              <div
                className="rounded-lg border cursor-pointer"
                onClick={() => openTriggerPanel(TriggerPanelTabs.schedules)}
              >
                <TriggerNodeSchedulesTable workflowId={workflow.id} />
              </div>
              {/* Case triggers */}
              {isFeatureEnabled("case-triggers") && (
                <div
                  className="rounded-lg border cursor-pointer"
                  onClick={() =>
                    openTriggerPanel(TriggerPanelTabs.caseTriggers)
                  }
                >
                  <TriggerNodeCaseTriggersTable
                    isLoading={caseTriggerIsLoading}
                    error={caseTriggerError}
                    enabled={isCaseTriggerEnabled}
                    hasConfig={hasCaseTriggerConfig}
                  />
                </div>
              )}
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
  isLoading,
  error,
  enabled,
  hasConfig,
}: {
  isLoading: boolean
  error: unknown
  enabled: boolean
  hasConfig: boolean
}) {
  if (isLoading) {
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
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center p-2">
        <UnplugIcon className="size-4 text-muted-foreground" />
      </div>
    )
  }

  return (
    <Table>
      <TableHeader className="[&_tr]:border-b-0">
        <TableRow className="border-b-0">
          <TableHead className="h-8 text-center text-xs" colSpan={2}>
            <div className="flex items-center justify-center gap-1">
              <SquarePlay className="size-3" />
              <span>Case triggers</span>
              <span
                className={cn(
                  "ml-1 inline-block size-2 rounded-full",
                  enabled ? "bg-emerald-500" : "bg-muted"
                )}
              />
            </div>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {!hasConfig && (
          <TableRow className="justify-center text-xs text-muted-foreground">
            <TableCell className="h-8 bg-muted-foreground/5 text-center">
              No case triggers
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  )
}
