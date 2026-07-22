"use client"

import { FileInputIcon, ShapesIcon } from "lucide-react"
import { useState } from "react"
import {
  ActionEventDetails,
  type ActionEventPayloadType,
} from "@/components/executions/action-event-details"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  WF_TRIGGER_EVENT_REF,
  type WorkflowExecutionEventCompact,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"

/** Props for the grouped workflow execution event detail pane. */
export interface WorkflowExecutionEventDetailViewProps {
  actionRef: string
  events: WorkflowExecutionEventCompact[]
  executionId: string
  executionStatus: WorkflowExecutionReadCompact["status"]
}

/** Render grouped action input and per-stream results in the run details page. */
export function WorkflowExecutionEventDetailView(
  props: WorkflowExecutionEventDetailViewProps
) {
  return (
    <WorkflowExecutionEventDetailTabs
      key={`${props.executionId}:${props.actionRef}`}
      {...props}
    />
  )
}

function WorkflowExecutionEventDetailTabs({
  actionRef,
  events,
  executionId,
  executionStatus,
}: WorkflowExecutionEventDetailViewProps) {
  const relatedEvents = events.filter((event) => event.action_ref === actionRef)
  const hasInput = relatedEvents.some(
    (event) => event.action_input !== null && event.action_input !== undefined
  )
  const hasResult =
    actionRef !== WF_TRIGGER_EVENT_REF && relatedEvents.length > 0
  const tabItems: {
    value: ActionEventPayloadType
    label: "Input" | "Result"
    icon: typeof FileInputIcon | typeof ShapesIcon
  }[] = []

  if (hasInput) {
    tabItems.push({
      value: "input",
      label: "Input",
      icon: FileInputIcon,
    })
  }
  if (hasResult) {
    tabItems.push({
      value: "result",
      label: "Result",
      icon: ShapesIcon,
    })
  }
  // Last pushed tab (result over input) is the preferred default.
  const [activeTab, setActiveTab] = useState<ActionEventPayloadType | null>(
    tabItems.at(-1)?.value ?? null
  )

  if (tabItems.length === 0 || !activeTab) {
    return (
      <div className="px-4 py-6 text-sm text-muted-foreground">
        No input or result is available for this event.
      </div>
    )
  }

  return (
    <div className="size-full min-h-0 overflow-hidden">
      <Tabs
        value={activeTab}
        onValueChange={(value) => {
          if (value === "input" || value === "result") {
            setActiveTab(value)
          }
        }}
        className="flex h-full min-h-0 flex-col"
      >
        <div className="sticky top-0 z-10 border-b bg-background">
          <TabsList className="inline-flex h-8 items-center justify-start bg-transparent px-0 py-0">
            {tabItems.map((tab) => (
              <TabsTrigger
                key={tab.value}
                value={tab.value}
                className="flex h-full min-w-20 items-center justify-start rounded-none px-3 py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              >
                <tab.icon className="mr-2 size-4" />
                <span>{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        {hasInput && (
          <TabsContent
            value="input"
            className="m-0 flex-1 overflow-auto p-3 data-[state=active]:overflow-auto"
          >
            <ActionEventDetails
              executionId={executionId}
              actionRef={actionRef}
              status={executionStatus}
              events={events}
              type="input"
              presentation="single"
            />
          </TabsContent>
        )}
        {hasResult && (
          <TabsContent
            value="result"
            className="m-0 flex-1 overflow-auto data-[state=active]:overflow-auto"
          >
            <ActionEventDetails
              executionId={executionId}
              actionRef={actionRef}
              status={executionStatus}
              events={events}
              type="result"
              presentation="single"
            />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
