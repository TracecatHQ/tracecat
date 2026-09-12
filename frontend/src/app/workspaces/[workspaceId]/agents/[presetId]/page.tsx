"use client"

import { useParams, useSearchParams } from "next/navigation"
import { useEffect } from "react"
import { AgentPresetsBuilder } from "@/components/agents/agent-presets-builder"

export default function AgentPresetsPage() {
  const params = useParams<{
    presetId: string
  }>()
  const searchParams = useSearchParams()
  const presetId = params?.presetId
  const builderPrompt = searchParams.get("builderPrompt") ?? undefined

  useEffect(() => {
    document.title = "Agent Presets"
  }, [])

  return (
    <div className="h-full">
      <AgentPresetsBuilder presetId={presetId} builderPrompt={builderPrompt} />
    </div>
  )
}
