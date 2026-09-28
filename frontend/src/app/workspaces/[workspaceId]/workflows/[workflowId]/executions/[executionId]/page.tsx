"use client"

import { useState } from "react"

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"

import "react18-json-view/src/style.css"

import { CalendarSearchIcon } from "lucide-react"
import { useParams } from "next/navigation"
import { WorkflowExecutionEventDetailView } from "@/components/executions/event-details"
import { WorkflowExecutionEventHistory } from "@/components/executions/event-history"
import { SectionHead } from "@/components/executions/section"
import { CenteredSpinner } from "@/components/loading/spinner"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"
import {
  formatExecutionId,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import { useCompactWorkflowExecution } from "@/lib/hooks"

const defaultLayout = [15, 24, 61]

export default function ExecutionPage() {
  const params = useParams<{
    workflowId: string
    executionId: string
  }>()

  if (!params) {
    return (
      <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">
          Invalid parameters
        </h1>
        <span className="text-center text-sm text-muted-foreground">
          Unable to load execution details.
        </span>
      </main>
    )
  }

  const { workflowId, executionId } = params
  const fullExecutionId = formatExecutionId(workflowId, executionId)

  return (
    <ExecutionDetails
      key={fullExecutionId}
      executionId={decodeURIComponent(fullExecutionId)}
    />
  )
}

function ExecutionDetails({ executionId }: { executionId: string }) {
  const [requestedActionRef, setRequestedActionRef] = useState<
    string | undefined
  >()
  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(executionId)
  const selectedActionRef =
    requestedActionRef &&
    execution?.events.some((event) => event.action_ref === requestedActionRef)
      ? requestedActionRef
      : undefined

  return (
    <ResizablePanelGroup
      direction="horizontal"
      className="h-full"
      onLayout={(sizes: number[]) => {
        document.cookie = `workflow-executions:react-resizable-panels:layout=${JSON.stringify(
          sizes
        )}`
      }}
    >
      {/* Panel 2: Events */}
      <ResizablePanel defaultSize={defaultLayout[1]} minSize={18}>
        <div className="flex h-full min-h-0 flex-col overflow-hidden">
          <SectionHead
            text="Events"
            icon={<CalendarSearchIcon className="size-4" strokeWidth={2} />}
          />
          <div className="min-h-0 flex-1 overflow-auto">
            <EventHistoryContent
              execution={execution}
              isLoading={executionIsLoading}
              error={executionError}
              selectedActionRef={selectedActionRef}
              setSelectedActionRef={setRequestedActionRef}
            />
          </div>
        </div>
      </ResizablePanel>
      <ResizableHandle withHandle />
      {/* Panel 3: Event Details */}
      <ResizablePanel
        defaultSize={defaultLayout[2]}
        minSize={25}
        className="grow"
      >
        <div className="flex h-full min-h-0 flex-col overflow-hidden">
          <div className="min-h-0 flex-1 overflow-auto">
            {selectedActionRef && execution ? (
              <WorkflowExecutionEventDetailView
                actionRef={selectedActionRef}
                events={execution.events}
                executionId={execution.id}
                executionStatus={execution.status}
              />
            ) : (
              <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
                <h1 className="text-lg font-semibold tracking-tight">
                  Select an event
                </h1>
                <span className="text-center text-sm text-muted-foreground">
                  Click on an event in events to view details.
                </span>
              </main>
            )}
          </div>
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

function EventHistoryContent({
  execution,
  isLoading,
  error,
  selectedActionRef,
  setSelectedActionRef,
}: {
  execution: WorkflowExecutionReadCompact | null | undefined
  isLoading: boolean
  error: Error | null
  selectedActionRef?: string
  setSelectedActionRef: (actionRef: string) => void
}) {
  if (isLoading) {
    return <CenteredSpinner />
  }
  if (error) {
    return <AlertNotification message={error.message} />
  }
  if (!execution) {
    return <NoContent message="No events found." />
  }
  return (
    <WorkflowExecutionEventHistory
      execution={execution}
      selectedActionRef={selectedActionRef}
      setSelectedActionRef={setSelectedActionRef}
    />
  )
}
