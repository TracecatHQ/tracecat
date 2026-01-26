"use client"

import { useEffect, useState } from "react"
import { agentSessionsListSessions } from "@/client"
import type { AgentSessionWithStatus } from "@/lib/agents"
import { useWorkspaceId } from "@/providers/workspace-id"
import { InboxDetail } from "./inbox-detail"

interface InboxChatProps {
  session: AgentSessionWithStatus
}

export function InboxChat({ session }: InboxChatProps) {
  const workspaceId = useWorkspaceId()

  // Track the forked session ID and pending message
  const [forkedState, setForkedState] = useState<{
    sessionId: string
    pendingMessage?: string
  } | null>(null)

  // Fetch existing forked session when session changes
  useEffect(() => {
    if (!session?.id || !workspaceId) return

    // Track whether this effect is still current to prevent stale responses
    // from overwriting state when the user switches sessions quickly
    let isCurrent = true

    const fetchForkedSession = async () => {
      try {
        const childSessions = await agentSessionsListSessions({
          workspaceId,
          parentSessionId: session.id,
          limit: 1,
        })
        // Only update state if this effect hasn't been superseded
        if (!isCurrent) return

        if (childSessions.length > 0) {
          setForkedState({ sessionId: childSessions[0].id })
        } else {
          setForkedState(null)
        }
      } catch (err) {
        // Only update state if this effect hasn't been superseded
        if (!isCurrent) return

        console.error("Failed to fetch forked session:", err)
        setForkedState(null)
      }
    }

    fetchForkedSession()

    return () => {
      isCurrent = false
    }
  }, [session?.id, workspaceId])

  const activeSessionId = forkedState?.sessionId ?? session.id

  const handleForked = (forkedId: string, pendingMessage: string) => {
    setForkedState({ sessionId: forkedId, pendingMessage })
  }

  const handlePendingMessageSent = () => {
    setForkedState((prev) =>
      prev ? { ...prev, pendingMessage: undefined } : null
    )
  }

  return (
    <InboxDetail
      key={activeSessionId}
      sessionId={activeSessionId}
      parentSessionId={session.id}
      session={session}
      onForked={handleForked}
      pendingMessage={forkedState?.pendingMessage}
      onPendingMessageSent={handlePendingMessageSent}
    />
  )
}
