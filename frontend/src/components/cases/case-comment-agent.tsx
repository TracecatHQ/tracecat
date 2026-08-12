"use client"

import {
  AlertCircleIcon,
  BotIcon,
  ClockIcon,
  LoaderCircleIcon,
} from "lucide-react"

import type {
  CaseCommentAgentAttributionRead,
  CaseCommentAgentInvocationRead,
  CaseCommentMentionRead,
} from "@/client"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useCaseChatSession } from "@/hooks/use-case-chat-session"

function CaseCommentAgentAvatar({ presetName }: { presetName: string }) {
  return (
    <Avatar
      role="img"
      aria-label={`Agent avatar for ${presetName}`}
      className="size-5"
    >
      <AvatarFallback className="text-muted-foreground">
        <BotIcon aria-hidden="true" className="size-3" />
      </AvatarFallback>
    </Avatar>
  )
}

function ViewAgentSession({
  presetName,
  sessionId,
}: {
  presetName: string
  sessionId: string
}) {
  const { openChatSession } = useCaseChatSession()

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-6 shrink-0 px-1.5 text-xs text-muted-foreground"
      aria-label={`View ${presetName} session`}
      onClick={() => openChatSession(sessionId)}
    >
      View session
    </Button>
  )
}

/** Render the snapshotted agent identity for a generated comment reply. */
export function CaseCommentAgentAttribution({
  attribution,
}: {
  attribution: CaseCommentAgentAttributionRead
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <CaseCommentAgentAvatar presetName={attribution.preset_name} />
      <span className="truncate text-sm font-medium text-foreground">
        {attribution.preset_name}
      </span>
      <Badge
        variant="secondary"
        className="h-5 shrink-0 rounded-full px-1.5 text-[10px] leading-none"
      >
        Agent
      </Badge>
      {attribution.session_id ? (
        <ViewAgentSession
          presetName={attribution.preset_name}
          sessionId={attribution.session_id}
        />
      ) : null}
    </div>
  )
}

function ActiveInvocationStatus({
  invocation,
}: {
  invocation: CaseCommentAgentInvocationRead
}) {
  const running = invocation.status === "running"

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground"
    >
      <CaseCommentAgentAvatar presetName={invocation.preset_name} />
      {running ? (
        <LoaderCircleIcon
          aria-hidden="true"
          className="size-3.5 animate-spin"
        />
      ) : (
        <ClockIcon aria-hidden="true" className="size-3.5 animate-pulse" />
      )}
      <span className="min-w-0 flex-1 truncate">
        <span className="font-medium text-foreground">
          {invocation.preset_name}
        </span>{" "}
        {running ? "is thinking..." : "is preparing..."}
      </span>
      {running && invocation.session_id ? (
        <ViewAgentSession
          presetName={invocation.preset_name}
          sessionId={invocation.session_id}
        />
      ) : null}
    </div>
  )
}

function FailedInvocationStatus({
  invocation,
}: {
  invocation: CaseCommentAgentInvocationRead
}) {
  return (
    <div role="alert" className="flex min-w-0 items-start gap-2 text-sm">
      <CaseCommentAgentAvatar presetName={invocation.preset_name} />
      <AlertCircleIcon
        aria-hidden="true"
        className="mt-0.5 size-3.5 shrink-0 text-destructive"
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="font-medium text-destructive">
          {invocation.preset_name} could not finish.
        </p>
        <p className="whitespace-pre-wrap break-words text-muted-foreground">
          {invocation.error?.message ??
            "The agent could not complete the request."}
        </p>
        <p className="text-xs text-muted-foreground">
          Mention {invocation.preset_name} again to retry.
        </p>
      </div>
      {invocation.session_id ? (
        <ViewAgentSession
          presetName={invocation.preset_name}
          sessionId={invocation.session_id}
        />
      ) : null}
    </div>
  )
}

function InvocationStatus({
  invocation,
}: {
  invocation: CaseCommentAgentInvocationRead
}) {
  switch (invocation.status) {
    case "pending":
    case "running":
      return <ActiveInvocationStatus invocation={invocation} />
    case "failed":
      return <FailedInvocationStatus invocation={invocation} />
    case "succeeded":
      return null
  }
}

/** Render non-successful agent invocations in persisted mention order. */
export function CaseCommentAgentInvocationList({
  mentions,
}: {
  mentions: CaseCommentMentionRead[] | undefined
}) {
  const invocations =
    mentions?.flatMap(({ invocation }) => (invocation ? [invocation] : [])) ??
    []
  const visibleInvocations = invocations.filter(
    ({ status }) => status !== "succeeded"
  )

  if (visibleInvocations.length === 0) {
    return null
  }

  return (
    <div aria-label="Agent activity" className="space-y-2 pt-1">
      {visibleInvocations.map((invocation) => (
        <InvocationStatus key={invocation.id} invocation={invocation} />
      ))}
    </div>
  )
}
