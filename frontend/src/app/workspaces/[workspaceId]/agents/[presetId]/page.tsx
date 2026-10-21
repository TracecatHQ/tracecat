"use client"

import { useParams } from "next/navigation"
import { useEffect } from "react"
import { AgentPresetsBuilder } from "@/components/agents/agent-presets-builder"

export default function AgentPresetsPage() {
  const params = useParams<{
    presetId: string
  }>()
  const presetId = params?.presetId

  useEffect(() => {
    document.title = "Agent Presets"
  }, [])

  return (
    <div className="h-full">
      <AgentPresetsBuilder presetId={presetId} />
    </div>
  )
}
