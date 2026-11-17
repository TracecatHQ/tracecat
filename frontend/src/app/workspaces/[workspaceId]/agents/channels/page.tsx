"use client"

import { WorkspaceChannelsView } from "@/components/agents/external-channels/workspace-channels-view"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function AgentChannelsPage() {
  const workspaceId = useWorkspaceId()
  return <WorkspaceChannelsView workspaceId={workspaceId} />
}
