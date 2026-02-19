"use client"

import { FileInputIcon, ShapesIcon, TriangleAlert } from "lucide-react"
import React from "react"
import JsonView from "react18-json-view"
import { CodeBlock } from "@/components/code-block"
import { ExternalObjectResult } from "@/components/executions/external-object-result"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"
import { isExternalStoredObject } from "@/lib/stored-object"

import "react18-json-view/src/style.css"

export function WorkflowExecutionEventDetailView({
  event,
  executionId,
}: {
  event: WorkflowExecutionEventCompact
  executionId: string
}) {
  const hasFailure = Boolean(event.action_error)
  const hasResult =
    event.action_result !== null && event.action_result !== undefined
  const hasInput =
    event.action_input !== null && event.action_input !== undefined
  const tabItems: {
    value: "input" | "result"
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

  const initialTab = tabItems[0]?.value
  const [activeTab, setActiveTab] = React.useState<"input" | "result" | null>(
    initialTab ?? null
  )

  React.useEffect(() => {
    setActiveTab(initialTab ?? null)
  }, [event.source_event_id, initialTab])

  return (
    <div className="size-full overflow-hidden">
      <div className="flex h-full flex-col">
        {hasFailure && (
          <div className="border-b">
            <div className="flex h-8 items-center border-b px-3 text-[11px] font-semibold text-muted-foreground">
              <TriangleAlert
                className="mr-2 size-4 fill-rose-500 stroke-white"
                strokeWidth={2}
              />
              <span>Event failure</span>
            </div>
            <div className="p-3">
              <CodeBlock title="Message">
                <span className="text-xs">
                  {event.action_error?.message ?? "No error message"}
                </span>
              </CodeBlock>
            </div>
          </div>
        )}

        {tabItems.length > 0 && activeTab ? (
          <Tabs
            value={activeTab}
            onValueChange={(value: string) => {
              setActiveTab(value as "input" | "result")
            }}
            className="flex min-h-0 flex-1 flex-col"
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
              <TabsContent value="input" className="m-0 flex-1 overflow-auto">
                <JsonViewContent src={event.action_input} />
              </TabsContent>
            )}
            {hasResult && (
              <TabsContent value="result" className="m-0 flex-1 overflow-auto">
                {isExternalStoredObject(event.action_result) ? (
                  <ExternalObjectResult
                    executionId={executionId}
                    eventId={event.source_event_id}
                    external={event.action_result}
                  />
                ) : (
                  <JsonViewContent src={event.action_result} />
                )}
              </TabsContent>
            )}
          </Tabs>
        ) : !hasFailure ? (
          <div className="px-4 py-6 text-sm text-muted-foreground">
            No input or result is available for this event.
          </div>
        ) : (
          <div className="px-4 py-4 text-sm text-muted-foreground">
            No input or result is available for this event.
          </div>
        )}
      </div>
    </div>
  )
}

function JsonViewContent({ src }: { src: unknown }): JSX.Element {
  return (
    <div className="border-b bg-muted-foreground/5 p-3">
      <JsonView
        collapsed={false}
        displaySize
        enableClipboard
        src={src}
        className="break-all text-xs"
        theme="atom"
      />
    </div>
  )
}
