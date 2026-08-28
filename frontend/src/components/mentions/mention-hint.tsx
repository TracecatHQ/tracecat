"use client"

import { Kbd } from "@/components/ui/kbd"
import type { MentionSourceState } from "@/hooks/use-mentions"

/**
 * Discoverability hint for the composer's mention triggers, shown only while
 * the textarea is focused and empty.
 *
 * A locked source still gets a row: the whole point of the lock state is that
 * an un-entitled org can find the feature before it can use it. Only a source
 * the surface never offers is omitted.
 */
export function MentionHint({
  show,
  agents,
  workflows,
}: {
  show: boolean
  agents: MentionSourceState
  workflows: MentionSourceState
}) {
  const showWorkflows = workflows !== "unavailable"
  const showAgents = agents !== "unavailable"
  if (!show || (!showWorkflows && !showAgents)) {
    return null
  }
  return (
    <div
      className="flex items-center gap-3 text-xs text-muted-foreground"
      data-testid="composer-hint"
    >
      {showWorkflows ? (
        <span className="flex items-center gap-1">
          <Kbd>/</Kbd>
          Run workflow
        </span>
      ) : null}
      {showAgents ? (
        <span className="flex items-center gap-1">
          <Kbd>@</Kbd>
          Mention agent
        </span>
      ) : null}
    </div>
  )
}
