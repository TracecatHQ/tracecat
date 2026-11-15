"use client"

import { Hash } from "lucide-react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { useSlackPlatformAppAvailable } from "@/hooks/use-agent-channel-bots"

/** Opens the workspace's Channels page, where Tracecat for Slack is available. */
export function AgentsChannelsButton({ workspaceId }: { workspaceId: string }) {
  const { slackPlatformAvailable } = useSlackPlatformAppAvailable(workspaceId)
  if (!slackPlatformAvailable) {
    return null
  }
  return (
    <Button variant="outline" size="sm" className="h-7 bg-background" asChild>
      <Link href={`/workspaces/${workspaceId}/agents/channels`}>
        <Hash className="mr-1 h-3.5 w-3.5" />
        Channels
      </Link>
    </Button>
  )
}
