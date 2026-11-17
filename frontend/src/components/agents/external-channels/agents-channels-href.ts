"use client"

import { useSlackPlatformAppAvailable } from "@/hooks/use-agent-channel-bots"

/** The workspace Channels page, when Tracecat for Slack is available there. */
export function useAgentsChannelsHref(workspaceId: string): string | undefined {
  const { slackPlatformAvailable } = useSlackPlatformAppAvailable(workspaceId)
  return slackPlatformAvailable
    ? `/workspaces/${workspaceId}/agents/channels`
    : undefined
}
