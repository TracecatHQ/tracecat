"use client"

import { useChannelBotReadsAllowed } from "@/hooks/use-agent-channel-bots"

/** The workspace Channels page, which lists custom bots even without Tracecat for Slack. */
export function useAgentsChannelsHref(workspaceId: string): string | undefined {
  return useChannelBotReadsAllowed()
    ? `/workspaces/${workspaceId}/agents/channels`
    : undefined
}
