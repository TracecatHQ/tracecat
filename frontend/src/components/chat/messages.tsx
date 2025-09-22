"use client"

import { useQueryClient } from "@tanstack/react-query"
import { MessageSquare } from "lucide-react"
import { motion } from "motion/react"
import Image from "next/image"
import TracecatIcon from "public/icon.png"
import { useEffect, useRef } from "react"
import { ModelMessagePart } from "@/components/builder/events/events-selected-action"
import { Dots } from "@/components/loading/dots"
import type { ModelMessage } from "@/lib/chat"

interface MessagesProps {
  messages: ModelMessage[]
  isResponding: boolean
  entityType: string
  entityId: string
  workspaceId: string
}

const caseUpdateActions = [
  "core__cases__update_case",
  "core__cases__create_comment",
]

const runbookUpdateActions = ["core__runbooks__update_runbook"]

export function Messages({
  messages,
  isResponding,
  entityType,
  entityId,
  workspaceId,
}: MessagesProps) {
  const queryClient = useQueryClient()
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
      inline: "nearest",
    })
  }, [messages])

  // Real-time invalidation when the agent updates a case or runbook
  // TODO: Make this generic and injectable from the parent component
  useEffect(() => {
    if (messages.length === 0) {
      return
    }

    // We have at least 1 message
    const lastMsg = messages[messages.length - 1]

    // Handle case updates (on tool-call or tool-return)
    if (
      entityType === "case" &&
      lastMsg.parts.some(
        (p) =>
          "tool_name" in p &&
          p.tool_name &&
          caseUpdateActions.includes(p.tool_name)
      )
    ) {
      console.log("Invalidating case queries")
      // Force-refetch the case & related queries so the UI updates instantly
      queryClient.invalidateQueries({ queryKey: ["case", entityId] })
      queryClient.invalidateQueries({ queryKey: ["cases", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["case-events", entityId, workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case-comments", entityId, workspaceId],
      })
    }

    // Handle runbook updates (on tool-call or tool-return)
    if (
      entityType === "runbook" &&
      lastMsg.parts.some(
        (p) =>
          "tool_name" in p &&
          p.tool_name &&
          runbookUpdateActions.includes(p.tool_name)
      )
    ) {
      console.log("Invalidating runbook queries")
      // Force-refetch the runbook & related queries so the UI updates instantly
      queryClient.invalidateQueries({ queryKey: ["runbooks"], exact: false })
    }
  }, [messages, entityType, entityId, workspaceId, queryClient])

  return (
    <div className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll p-4 relative">
      {messages.length === 0 && <NoMessages />}
      {messages.map((message, index) => (
        <ModelMessagePart key={index} part={message} />
      ))}
      {isResponding && (
        <motion.div
          className="flex gap-3 items-center"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.3, ease: "easeInOut" }}
        >
          <Image src={TracecatIcon} alt="Tracecat" className="size-4" />
          <Dots />
        </motion.div>
      )}
      <div ref={messagesEndRef} />
    </div>
  )
}

function NoMessages() {
  return (
    <div className="flex h-full items-center justify-center text-center">
      <div className="max-w-sm">
        <MessageSquare className="mx-auto h-8 w-8 text-gray-400 mb-3" />
        <h4 className="text-sm font-medium text-gray-900 mb-1">
          Start a conversation
        </h4>
        <p className="text-xs text-gray-500">
          Ask me anything or get help with your tasks.
        </p>
      </div>
    </div>
  )
}
