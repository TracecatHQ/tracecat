"use client"

import { useEffect, useState } from "react"
import type { ImperativePanelHandle } from "react-resizable-panels"
import {
  buildEventsTabItems,
  EventsLoading,
  type EventsSidebarTabs,
  useResolvedLastExecution,
} from "@/components/builder/events/events-shared"
import { EventsSidebarEmpty } from "@/components/builder/events/events-sidebar-empty"
import { AlertNotification } from "@/components/notifications"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useCompactWorkflowExecution, useOrgAppSettings } from "@/lib/hooks"
import { useWorkflowBuilder } from "@/providers/builder"

export type { EventsSidebarTabs }

/**
 * Interface for controlling the events sidebar through a ref
 */
export interface EventsSidebarRef extends ImperativePanelHandle {
  /** Sets the active tab in the events sidebar */
  setActiveTab: (tab: EventsSidebarTabs) => void
  /** Gets the current active tab */
  getActiveTab: () => EventsSidebarTabs
  /** Sets the open state of the events sidebar */
  setOpen: (open: boolean) => void
  /** Gets the open state of the events sidebar */
  isOpen: () => boolean
}

export function BuilderSidebarEvents() {
  const { sidebarRef } = useWorkflowBuilder()
  const [activeTab, setActiveTab] =
    useState<EventsSidebarTabs>("workflow-events")
  const [open, setOpen] = useState(false)
  const resolved = useResolvedLastExecution()

  // Set up the ref methods
  useEffect(() => {
    if (sidebarRef.current) {
      sidebarRef.current.setActiveTab = setActiveTab
      sidebarRef.current.getActiveTab = () => activeTab
      sidebarRef.current.setOpen = (newOpen: boolean) => {
        setOpen(newOpen)
        if (sidebarRef.current?.collapse && sidebarRef.current?.expand) {
          newOpen ? sidebarRef.current.expand() : sidebarRef.current.collapse()
        }
      }
      sidebarRef.current.isOpen = () => open
    }
  }, [sidebarRef, activeTab, setOpen, open])

  if (resolved.status === "pending") {
    return resolved.node
  }

  return (
    <BuilderSidebarEventsList
      activeTab={activeTab}
      executionId={resolved.executionId}
    />
  )
}

function BuilderSidebarEventsList({
  activeTab,
  executionId,
}: {
  activeTab: EventsSidebarTabs
  executionId: string
}) {
  const { appSettings } = useOrgAppSettings()
  const { sidebarRef } = useWorkflowBuilder()

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
  const tabItems = buildEventsTabItems({
    execution,
    interactionsEnabled: !!appSettings?.app_interactions_enabled,
  })

  return (
    <div className="h-full">
      <Tabs
        value={activeTab}
        onValueChange={(value: string) => {
          if (sidebarRef.current?.setActiveTab) {
            sidebarRef.current.setActiveTab(value as EventsSidebarTabs)
          }
        }}
        className="flex size-full flex-col"
      >
        <div className="sticky top-0 z-10 mt-0.5 bg-background">
          <ScrollArea className="w-full whitespace-nowrap rounded-md">
            <TabsList className="inline-flex h-8 flex-1 items-center justify-start bg-transparent p-0">
              {tabItems.map((tab) => (
                <TabsTrigger
                  key={tab.value}
                  value={tab.value}
                  className="flex h-full min-w-20 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none sm:min-w-16 md:min-w-20"
                >
                  <tab.icon className="mr-2 size-4 sm:mr-1" />
                  <span className="hidden sm:inline">{tab.label}</span>
                  <span className="sm:hidden">{tab.label.slice(0, 4)}</span>
                </TabsTrigger>
              ))}
            </TabsList>
            <ScrollBar orientation="horizontal" className="invisible" />
          </ScrollArea>
        </div>
        <Separator />
        <div className="size-full overflow-scroll">
          {tabItems.map((tab) => (
            <TabsContent
              key={tab.value}
              value={tab.value}
              className="m-0 size-full min-w-[200px] p-0"
            >
              {tab.content}
            </TabsContent>
          ))}
        </div>
      </Tabs>
    </div>
  )
}
