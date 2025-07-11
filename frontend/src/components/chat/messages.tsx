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
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  // Real-time invalidation when the agent updates a case
  // TODO: Make this generic and injectable from the parent component
  useEffect(() => {
    if (entityType !== "case" || messages.length === 0) {
      return
    }

    // We have at least 1 message
    const lastMsg = messages[messages.length - 1]

    if (
      lastMsg.kind === "response" &&
      lastMsg.parts.some(
        (p) => "tool_name" in p && p.tool_name === "core__cases__update_case"
      )
    ) {
      console.log("Invalidating case queries")
      // Force-refetch the case & related queries so the UI updates instantly
      queryClient.invalidateQueries({ queryKey: ["case", entityId] })
      queryClient.invalidateQueries({ queryKey: ["cases", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["case-events", entityId, workspaceId],
      })
    }
  }, [messages, entityType, entityId, workspaceId, queryClient])

  return (
    <div className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll p-4 relative no-scrollbar">
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
          Ask me anything about this case or get help with investigation tasks.
        </p>
      </div>
    </div>
  )
}
