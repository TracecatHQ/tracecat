"use client"

import {
  CalendarSearchIcon,
  FileInputIcon,
  type LucideIcon,
  MessagesSquare,
  ShapesIcon,
  WorkflowIcon,
} from "lucide-react"
import type { ReactNode } from "react"
import { $TriggerType, type TriggerType } from "@/client"
import { ActionEventPane } from "@/components/builder/events/events-selected-action"
import { WorkflowInteractions } from "@/components/builder/events/events-sidebar-interactions"
import {
  WorkflowEvents,
  WorkflowEventsHeader,
} from "@/components/builder/events/events-workflow"
import { Spinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useLocalStorage } from "@/hooks/use-local-storage"
import type { WorkflowExecutionReadCompact } from "@/lib/event-history"
import { useLastExecution } from "@/lib/hooks"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"

// Define the available trigger types for UI generation
const AVAILABLE_TRIGGER_TYPES: readonly TriggerType[] = $TriggerType.enum

export type EventsSidebarTabs =
  | "workflow-events"
  | "action-input"
  | "action-result"
  | "action-interaction"

/** A single tab in the workflow events viewer. */
export interface EventsTabItem {
  value: EventsSidebarTabs
  label: string
  icon: LucideIcon
  content: ReactNode
}

/**
 * Resolution of the execution to show in an events viewer. Returns a `node` to
 * render directly while the last execution is loading, errored, or absent;
 * otherwise an `executionId` ready to fetch detailed events for.
 */
export type ResolvedLastExecution =
  | { status: "pending"; node: ReactNode }
  | { status: "ready"; executionId: string }

/** Centered spinner with a status message, used across events viewers. */
export function EventsLoading({ message }: { message: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center space-y-2">
      <span className="text-xs text-muted-foreground">{message}</span>
      <Spinner className="size-6" />
    </div>
  )
}

/**
 * Resolve which execution an events viewer should display for the workflow in
 * context: a directly-triggered run if present, otherwise the last execution
 * matching the user's selected trigger types. Shared by the builder events
 * sidebar and the embedded workflow artifact so their loading, error, and empty
 * states stay in sync.
 */
export function useResolvedLastExecution(): ResolvedLastExecution {
  const { workflowId } = useWorkflow()
  const { currentExecutionId } = useWorkflowBuilder()
  const [selectedTriggerTypes] = useLocalStorage<TriggerType[]>(
    "selected-trigger-types",
    [...AVAILABLE_TRIGGER_TYPES]
  )

  const { lastExecution, lastExecutionIsLoading, lastExecutionError } =
    useLastExecution({
      // Prefer the direct execution id (e.g. a run just triggered) over the query.
      workflowId: currentExecutionId ? undefined : workflowId,
      triggerTypes: selectedTriggerTypes,
    })

  const executionId = currentExecutionId || lastExecution?.id

  if (!currentExecutionId && lastExecutionIsLoading) {
    return {
      status: "pending",
      node: <EventsLoading message="Fetching last execution..." />,
    }
  }

  if (!currentExecutionId && lastExecutionError) {
    return {
      status: "pending",
      node: (
        <AlertNotification
          level="error"
          message={`Error loading last execution: ${lastExecutionError.message}`}
        />
      ),
    }
  }

  if (!executionId) {
    return { status: "pending", node: <NoWorkflowRuns /> }
  }

  return { status: "ready", executionId }
}

/** Empty state shown when a workflow has never run. */
function NoWorkflowRuns() {
  return (
    <div className="flex h-full items-center justify-center">
      <Empty className="border-none">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <WorkflowIcon />
          </EmptyMedia>
          <EmptyTitle>No workflow runs</EmptyTitle>
          <EmptyDescription>
            Get started by running your workflow
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    </div>
  )
}

/**
 * Build the tab descriptors (Events / Input / Result / Interaction) for a
 * workflow execution. The interaction tab is only included when interactions
 * are enabled, and the header switches to its compact form when `embedded`.
 */
export function buildEventsTabItems({
  execution,
  interactionsEnabled,
  embedded = false,
}: {
  execution: WorkflowExecutionReadCompact
  interactionsEnabled: boolean
  embedded?: boolean
}): EventsTabItem[] {
  const tabItems: EventsTabItem[] = [
    {
      value: "workflow-events",
      label: "Events",
      icon: CalendarSearchIcon,
      content: (
        <>
          <WorkflowEventsHeader execution={execution} embedded={embedded} />
          {interactionsEnabled && (
            <WorkflowInteractions execution={execution} />
          )}
          <WorkflowEvents events={execution.events} status={execution.status} />
        </>
      ),
    },
    {
      value: "action-input",
      label: "Input",
      icon: FileInputIcon,
      content: <ActionEventPane execution={execution} type="input" />,
    },
    {
      value: "action-result",
      label: "Result",
      icon: ShapesIcon,
      content: <ActionEventPane execution={execution} type="result" />,
    },
  ]
  if (interactionsEnabled) {
    tabItems.push({
      value: "action-interaction",
      label: "Interaction",
      icon: MessagesSquare,
      content: <ActionEventPane execution={execution} type="interaction" />,
    })
  }
  return tabItems
}
