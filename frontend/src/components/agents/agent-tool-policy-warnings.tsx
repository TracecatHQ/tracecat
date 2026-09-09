"use client"

import { AlertCircle } from "lucide-react"
import type { AgentPresetToolPolicyRead } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

/** Shared preview state displayed by both preset configuration panels. */
export interface AgentToolPolicyPreviewState {
  data?: AgentPresetToolPolicyRead
  isPending: boolean
  isError: boolean
}

/** Display server-evaluated tool restrictions and their authored or skill source. */
export function AgentToolPolicyWarnings({
  preview,
}: {
  preview: AgentToolPolicyPreviewState
}) {
  if (preview.isError) {
    return <p>Unable to check tool policy. Please try again.</p>
  }
  if (preview.isPending || !preview.data) {
    return <p>Checking tool policy…</p>
  }
  const { blocked_tools = [], internet_sources = [] } = preview.data
  const skillInternetSources = internet_sources.filter(
    (source) => source.skill_id
  )
  if (blocked_tools.length === 0 && skillInternetSources.length === 0) {
    return null
  }
  return (
    <div className="flex flex-col gap-3" aria-live="polite">
      {blocked_tools.length > 0 ? (
        <Alert variant="warning">
          <AlertCircle />
          <AlertTitle>Tools blocked by namespace policy</AlertTitle>
          <AlertDescription>
            <p>The namespace policy blocks these tools:</p>
            <ul className="list-disc pl-5">
              {blocked_tools.map((source) => (
                <li
                  key={`${source.skill_id ?? "preset"}:${source.tool_id}`}
                  className="break-all"
                >
                  <code>{source.tool_id}</code> from{" "}
                  {source.skill_name
                    ? `skill “${source.skill_name}”`
                    : "the preset's action list"}
                </li>
              ))}
            </ul>
            <p>The agent can run, but these tools will be unavailable.</p>
          </AlertDescription>
        </Alert>
      ) : null}
      {skillInternetSources.length > 0 ? (
        <Alert variant="warning">
          <AlertCircle />
          <AlertTitle>Skill tools require internet access</AlertTitle>
          <AlertDescription>
            <p>
              Internet access is enabled because these attached skill tools
              require it:
            </p>
            <ul className="list-disc pl-5">
              {skillInternetSources.map((source) => (
                <li
                  key={`${source.skill_id}:${source.tool_id}`}
                  className="break-all"
                >
                  <code>{source.tool_id}</code> from skill “{source.skill_name}”
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  )
}
