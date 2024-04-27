"use client"

import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"
import { useWorkflowMetadata } from "@/providers/workflow"

import { streamGenerator } from "@/lib/api"
import {
  deleteFromLocalStorage,
  loadFromLocalStorage,
  storeInLocalStorage,
} from "@/lib/utils"
import {
  consoleEventSchema,
  GenericConsoleEvent,
} from "@/components/console/console"

interface EventFeedContextType {
  events: GenericConsoleEvent[] | null
  setEvents: React.Dispatch<SetStateAction<GenericConsoleEvent[] | null>>
  clearEvents: () => void
  isStreaming: boolean
}

const EventFeedContext = createContext<EventFeedContextType | undefined>(
  undefined
)

interface EventFeedProviderProps {
  children: ReactNode
}

export const EventFeedProvider: React.FC<EventFeedProviderProps> = ({
  children,
}) => {
  const { workflowId } = useWorkflowMetadata()
  const [events, setEvents] = useState<GenericConsoleEvent[] | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)

  useEffect(() => {
    // Important: null state means switching workflows, don't overwrite
    if (events) {
      storeInLocalStorage(`workflow-${workflowId}`, events)
    }
  }, [events])

  useEffect(() => {
    let stopSignal = false
    const fetchEvents = async () => {
      console.log(`Start streaming for workflow ${workflowId}`)
      setIsStreaming(() => true)

      const generator = streamGenerator("/events/subscribe", {
        method: "GET",
      })

      try {
        for await (const chunk of generator) {
          if (stopSignal) {
            console.log("Stop signal received, stopping stream")
            break
          }
          const jsonChunk = JSON.parse(chunk)
          const consoleEvent = consoleEventSchema.parse(jsonChunk)
          setEvents((events) => [...(events || []), consoleEvent])
        }
      } catch (error) {
        console.error("Error reading stream:", error)
        setIsStreaming(() => false)
      }
    }
    // When workflowId first changes, load from local storage
    setEvents(() => loadFromLocalStorage(`workflow-${workflowId}`))
    fetchEvents()
    return () => {
      // When swithcing off the workflow, cleanup the events
      stopSignal = true
      setEvents(() => null)
      setIsStreaming(() => false)
      console.log("Cleaned up event feed stream for workflow: ", workflowId)
    }
  }, [workflowId])

  const clearEvents = useCallback(() => {
    deleteFromLocalStorage(`workflow-${workflowId}`)
    setEvents(() => null)
  }, [workflowId, setEvents])

  return (
    <EventFeedContext.Provider
      value={{
        events,
        setEvents,
        clearEvents,
        isStreaming,
      }}
    >
      {children}
    </EventFeedContext.Provider>
  )
}

export const useEventFeedContext = (): EventFeedContextType => {
  const context = useContext(EventFeedContext)
  if (context === undefined) {
    throw new Error("useEventFeed must be used within a EventFeedProvider")
  }
  return context
}
