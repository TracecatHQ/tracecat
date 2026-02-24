"use client"

import React from "react"

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
import {
  formatExecutionId,
  type WorkflowExecutionEventCompact,
} from "@/lib/event-history"

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

  const [selectedEvent, setSelectedEvent] = React.useState<
    WorkflowExecutionEventCompact | undefined
  >()

  const fullExecutionId = formatExecutionId(workflowId, executionId)
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
            {fullExecutionId ? (
              <WorkflowExecutionEventHistory
                executionId={fullExecutionId}
                selectedEvent={selectedEvent}
                setSelectedEvent={setSelectedEvent}
              />
            ) : (
              <span className="flex justify-center p-4 text-center text-xs text-muted-foreground">
                Select a workflow execution.
              </span>
            )}
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
            {selectedEvent ? (
              <WorkflowExecutionEventDetailView
                event={selectedEvent}
                executionId={fullExecutionId}
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
