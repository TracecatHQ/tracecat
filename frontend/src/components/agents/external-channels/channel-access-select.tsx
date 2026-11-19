"use client"

import type { AgentPresetRead, ChannelAccessPolicy } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useSetAgentPresetChannelAccess } from "@/hooks/use-agent-preset-channel-access"

const POLICIES: ReadonlyArray<[ChannelAccessPolicy, string]> = [
  ["linked_approvals", "Anyone can message; linked members approve"],
  ["linked_only", "Only linked members"],
  ["open", "Anyone in the channel"],
]

/** Workspace policy, not package config: editable on managed presets too. */
export function ChannelAccessSelect({
  workspaceId,
  preset,
}: {
  workspaceId: string
  preset: AgentPresetRead
}) {
  const canUpdateAgent = useScopeCheck("agent:update") === true
  const { setPolicy, setPolicyIsPending } = useSetAgentPresetChannelAccess(
    workspaceId,
    preset.id
  )
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium">Who can use this agent</span>
      <Select
        value={preset.channel_access_policy}
        onValueChange={(value) => {
          const policy = POLICIES.find(([option]) => option === value)?.[0]
          if (policy) setPolicy(policy)
        }}
        disabled={!canUpdateAgent || setPolicyIsPending}
      >
        <SelectTrigger className="h-8 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {POLICIES.map(([value, label]) => (
            <SelectItem key={value} value={value} className="text-xs">
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">
        Members link their channel account by signing in to Tracecat.
      </p>
    </div>
  )
}
