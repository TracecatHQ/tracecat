"use client"

import { MousePointerClickIcon } from "lucide-react"
import Link from "next/link"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Preset identity recorded on an agent run's input. */
export type AgentPresetRef = { presetId: string | null; slug: string | null }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null
}

/** Read the preset an agent run used from its `AgentWorkflowArgs` input. */
export function getAgentPresetRef(
  event: WorkflowExecutionEventCompact
): AgentPresetRef | null {
  // Both ai.agent and ai.preset_agent runs are reported as ai.agent.
  if (event.action_name !== "ai.agent") return null
  const input = event.action_input
  if (!isRecord(input)) return null
  const presetId = asString(input.agent_preset_id)
  const slug = isRecord(input.agent_args)
    ? asString(input.agent_args.preset_slug)
    : null
  if (!presetId && !slug) return null
  return { presetId, slug }
}

/** Show the agent preset behind an agent run, linked to its page. */
export function AgentPresetEventLink({
  presetRef,
}: {
  presetRef: AgentPresetRef
}) {
  const workspaceId = useWorkspaceId()
  const canReadAgents = useScopeCheck("agent:read") === true
  // Every agent event mounts one; avoid a list refetch per mount.
  const { presets } = useAgentPresets(workspaceId, {
    enabled: canReadAgents && presetRef.presetId !== null,
    staleTime: 5 * 60 * 1000,
  })

  // Match by ID only: slugs are reusable after delete, so slug-only runs stay unlinked.
  const preset = presets?.find((p) => p.id === presetRef.presetId)
  const slug = presetRef.slug ?? preset?.slug

  let label = null
  if (preset) {
    label = (
      <Link
        href={`/workspaces/${workspaceId}/agents/${preset.id}`}
        className="truncate font-medium text-foreground hover:underline"
      >
        {preset.name}
      </Link>
    )
  } else if (presets) {
    label = <span className="italic">Deleted agent</span>
  }

  return (
    <div className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
      <MousePointerClickIcon className="size-3.5 shrink-0" />
      {label}
      {slug && <span className="truncate font-mono">{slug}</span>}
    </div>
  )
}
