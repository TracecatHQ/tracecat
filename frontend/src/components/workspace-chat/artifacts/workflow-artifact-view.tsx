"use client"

import { ReactFlowProvider } from "@xyflow/react"
import { WorkflowIcon } from "lucide-react"
import { $TriggerType, type TriggerType } from "@/client"
import { WorkflowCanvas } from "@/components/builder/canvas/canvas"
import { EventsSidebarEmpty } from "@/components/builder/events/events-sidebar-empty"
import { WorkflowInteractions } from "@/components/builder/events/events-sidebar-interactions"
import {
  WorkflowEvents,
  WorkflowEventsHeader,
} from "@/components/builder/events/events-workflow"
import { WorkflowBuilderErrorBoundary } from "@/components/error-boundaries"
import { Spinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { useLocalStorage } from "@/hooks/use-local-storage"
import {
  useCompactWorkflowExecution,
  useLastExecution,
  useOrgAppSettings,
} from "@/lib/hooks"
import {
  useWorkflowBuilder,
  WorkflowBuilderProvider,
} from "@/providers/builder"
import { useWorkflow, WorkflowProvider } from "@/providers/workflow"

const AVAILABLE_TRIGGER_TYPES: readonly TriggerType[] = $TriggerType.enum

/**
 * Embedded workflow artifact: the workflow DAG on top and a compact events
 * viewer for its latest run on the bottom, split like the agent builder.
 *
 * Mounts its own workflow + builder providers keyed to `workflowId` so the
 * canvas and events stay isolated from the surrounding chat surface.
 */
export function WorkflowArtifactView({
  workflowId,
  workspaceId,
}: {
  workflowId: string
  workspaceId: string
}) {
  return (
    // Remount per workflow so builder/canvas state never bleeds across artifacts.
    <WorkflowBuilderErrorBoundary key={workflowId}>
      <WorkflowProvider workspaceId={workspaceId} workflowId={workflowId}>
        <ReactFlowProvider>
          <WorkflowBuilderProvider>
            <ResizablePanelGroup direction="vertical" className="size-full">
              <ResizablePanel
                defaultSize={62}
                minSize={30}
                className="overflow-hidden"
              >
                <WorkflowArtifactCanvas />
              </ResizablePanel>

              <ResizableHandle withHandle />

              <ResizablePanel
                defaultSize={38}
                minSize={20}
                className="overflow-hidden"
              >
                <CompactWorkflowEvents />
              </ResizablePanel>
            </ResizablePanelGroup>
          </WorkflowBuilderProvider>
        </ReactFlowProvider>
      </WorkflowProvider>
    </WorkflowBuilderErrorBoundary>
  )
}

function WorkflowArtifactCanvas() {
  const { canvasRef } = useWorkflowBuilder()
  return <WorkflowCanvas ref={canvasRef} embedded />
}

/** Latest-run event timeline for the embedded workflow, without sidebar chrome. */
function CompactWorkflowEvents() {
  const { workflowId } = useWorkflow()
  const { currentExecutionId } = useWorkflowBuilder()
  const [selectedTriggerTypes] = useLocalStorage<TriggerType[]>(
    "selected-trigger-types",
    [...AVAILABLE_TRIGGER_TYPES]
  )

  const { lastExecution, lastExecutionIsLoading, lastExecutionError } =
    useLastExecution({
      workflowId: currentExecutionId ? undefined : workflowId,
      triggerTypes: selectedTriggerTypes,
    })

  // Prefer a direct execution id (e.g. a run just triggered) over the query.
  const executionId = currentExecutionId || lastExecution?.id

  if (!currentExecutionId && lastExecutionIsLoading) {
    return <EventsLoading message="Fetching last execution..." />
  }

  if (!currentExecutionId && lastExecutionError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading last execution: ${lastExecutionError.message}`}
      />
    )
  }

  if (!executionId) {
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

  return <CompactWorkflowEventsList executionId={executionId} />
}

function CompactWorkflowEventsList({ executionId }: { executionId: string }) {
  const { appSettings } = useOrgAppSettings()
  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(executionId)

  if (executionIsLoading) {
    return <EventsLoading message="Fetching events..." />
  }
  if (executionError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading execution: ${executionError.message}`}
      />
    )
  }
  if (!execution) {
    return (
      <EventsSidebarEmpty
        title="Could not find execution"
        description="Please refresh the page and try again"
      />
    )
  }

  return (
    <div className="size-full overflow-y-auto">
      <WorkflowEventsHeader execution={execution} />
      {appSettings?.app_interactions_enabled && (
        <WorkflowInteractions execution={execution} />
      )}
      <WorkflowEvents events={execution.events} status={execution.status} />
    </div>
  )
}

function EventsLoading({ message }: { message: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center space-y-2">
      <span className="text-xs text-muted-foreground">{message}</span>
      <Spinner className="size-6" />
    </div>
  )
}
