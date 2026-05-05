import type { Metadata } from "next"
import { AgentTagsView } from "@/components/agents/agent-tags-view"

export const metadata: Metadata = {
  title: "Agent tags",
}

export default function AgentTagsPage() {
  return <AgentTagsView />
}
