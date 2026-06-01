"use client"

import { ReactFlowProvider } from "@xyflow/react"
import {
  CalendarSearchIcon,
  FileInputIcon,
  MessagesSquare,
  ShapesIcon,
  WorkflowIcon,
} from "lucide-react"
import { type MutableRefObject, useEffect, useState } from "react"
import { $TriggerType, type TriggerType } from "@/client"
import { WorkflowCanvas } from "@/components/builder/canvas/canvas"
import { ActionEventPane } from "@/components/builder/events/events-selected-action"
import type {
  EventsSidebarRef,
  EventsSidebarTabs,
} from "@/components/builder/events/events-sidebar"
import { EventsSidebarEmpty } from "@/components/builder/events/events-sidebar-empty"
import { WorkflowInteractions } from "@/components/builder/events/events-sidebar-interactions"
import {
  WorkflowEvents,
  WorkflowEventsHeader,
} from "@/components/builder/events/events-workflow"
import { BuilderPanel } from "@/components/builder/panel/builder-panel"
import { WorkflowBuilderErrorBoundary } from "@/components/error-boundaries"
import { Spinner } from "@/components/loading/spinner"
import { WorkflowManualTrigger } from "@/components/nav/builder-nav"
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
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
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
            <div className="flex size-full flex-col">
              <WorkflowArtifactToolbar workflowId={workflowId} />
              <div className="min-h-0 flex-1">
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
                    <WorkflowArtifactBottomPanel />
                  </ResizablePanel>
                </ResizablePanelGroup>
              </div>
            </div>
          </WorkflowBuilderProvider>
        </ReactFlowProvider>
      </WorkflowProvider>
    </WorkflowBuilderErrorBoundary>
  )
}

/** Top toolbar with the manual run trigger for the embedded workflow. */
function WorkflowArtifactToolbar({ workflowId }: { workflowId: string }) {
  const { setSelectedNodeId } = useWorkflowBuilder()
  return (
    <div className="flex h-10 shrink-0 items-center justify-end gap-2 border-b px-3">
      <WorkflowManualTrigger
        disabled={false}
        workflowId={workflowId}
        // Reveal the events panel once a run starts by clearing the selection.
        onAfterTrigger={() => setSelectedNodeId(null)}
      />
    </div>
  )
}

function WorkflowArtifactCanvas() {
  const { canvasRef } = useWorkflowBuilder()
  return <WorkflowCanvas ref={canvasRef} embedded />
}

/**
 * Bottom panel: action inputs for the selected node, or the latest-run events
 * when nothing is selected. Mirrors the builder's action panel / events split
 * in the limited vertical space of the artifact.
 */
function WorkflowArtifactBottomPanel() {
  const { selectedNodeId } = useWorkflowBuilder()
  if (selectedNodeId) {
    return <BuilderPanel />
  }
  return <CompactWorkflowEvents />
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
  const { sidebarRef } = useWorkflowBuilder()
  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(executionId)
  const [activeTab, setActiveTab] =
    useState<EventsSidebarTabs>("workflow-events")

  // The shared events sidebar isn't mounted in the embedded artifact, so wire a
  // lightweight shim onto sidebarRef. This lets the events list's row actions
  // ("View last input/result") drive the compact tabs below.
  useEffect(() => {
    const ref = sidebarRef as MutableRefObject<EventsSidebarRef | null>
    ref.current = {
      setActiveTab,
      getActiveTab: () => activeTab,
      setOpen: () => {},
      isOpen: () => true,
      collapse: () => {},
      expand: () => {},
      getId: () => "embedded-events",
      getSize: () => 0,
      isCollapsed: () => false,
      isExpanded: () => true,
      resize: () => {},
    }
    return () => {
      ref.current = null
    }
  }, [sidebarRef, activeTab])

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

  const tabItems = [
    {
      value: "workflow-events" as EventsSidebarTabs,
      label: "Events",
      icon: CalendarSearchIcon,
      content: (
        <>
          <WorkflowEventsHeader execution={execution} embedded />
          {appSettings?.app_interactions_enabled && (
            <WorkflowInteractions execution={execution} />
          )}
          <WorkflowEvents events={execution.events} status={execution.status} />
        </>
      ),
    },
    {
      value: "action-input" as EventsSidebarTabs,
      label: "Input",
      icon: FileInputIcon,
      content: <ActionEventPane execution={execution} type="input" />,
    },
    {
      value: "action-result" as EventsSidebarTabs,
      label: "Result",
      icon: ShapesIcon,
      content: <ActionEventPane execution={execution} type="result" />,
    },
  ]
  if (appSettings?.app_interactions_enabled) {
    tabItems.push({
      value: "action-interaction",
      label: "Interaction",
      icon: MessagesSquare,
      content: <ActionEventPane execution={execution} type="interaction" />,
    })
  }

  return (
    <Tabs
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as EventsSidebarTabs)}
      className="flex size-full flex-col"
    >
      <div className="sticky top-0 z-10 mt-0.5 bg-background">
        <ScrollArea className="w-full whitespace-nowrap rounded-md">
          <TabsList className="inline-flex h-8 items-center justify-start bg-transparent p-0">
            {tabItems.map((tab) => (
              <TabsTrigger
                key={tab.value}
                value={tab.value}
                className="flex h-full min-w-16 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              >
                <tab.icon className="mr-1.5 size-4" />
                <span>{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>
          <ScrollBar orientation="horizontal" className="invisible" />
        </ScrollArea>
      </div>
      <Separator />
      <div className="size-full overflow-auto">
        {tabItems.map((tab) => (
          <TabsContent
            key={tab.value}
            value={tab.value}
            className="m-0 size-full p-0"
          >
            {tab.content}
          </TabsContent>
        ))}
      </div>
    </Tabs>
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
