"use client"

import React from "react"
import { EventHistoryRead } from "@/client"

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"

import "react18-json-view/src/style.css"

import { useParams } from "next/navigation"
import { History } from "lucide-react"

import { WorkflowExecutionEventDetailView } from "@/components/executions/event-details"
import { WorkflowExecutionEventHistory } from "@/components/executions/event-history"
import { SectionHead } from "@/components/executions/section"

const defaultLayout = [15, 15, 70]

export default function ExecutionPage() {
  const { workflowId, executionId } = useParams<{
    workflowId: string
    executionId: string
  }>()

  const [selectedEvent, setSelectedEvent] = React.useState<
    EventHistoryRead | undefined
  >()

  const fullExecutionId = `${workflowId}:${executionId}`
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
      {/* Panel 2: Event History */}
      <ResizablePanel defaultSize={defaultLayout[1]} minSize={15}>
        <div className="flex h-full flex-col overflow-hidden p-2">
          <div className="flex-none">
            <SectionHead
              text="Event History"
              icon={<History className="mr-2 size-4" strokeWidth={2} />}
            />
          </div>
          <div className="flex-1 overflow-auto">
            {fullExecutionId ? (
              <WorkflowExecutionEventHistory
                executionId={fullExecutionId}
                selectedEvent={selectedEvent}
                setSelectedEvent={setSelectedEvent}
              />
            ) : (
              <span className="flex justify-center p-4 text-center text-xs text-muted-foreground">
                Select a Workflow Execution.
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
        <div className="flex h-full flex-col overflow-hidden">
          <div className="flex-1 overflow-auto">
            {selectedEvent ? (
              <WorkflowExecutionEventDetailView event={selectedEvent} />
            ) : (
              <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
                <h1 className="text-lg font-semibold tracking-tight">
                  Select an event
                </h1>
                <span className="text-center text-sm text-muted-foreground">
                  Click on an event in the event history to view details.
                </span>
              </main>
            )}
          </div>
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
