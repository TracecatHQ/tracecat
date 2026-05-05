"use client"

import { DiffViewer } from "@/components/common/diff-viewer"

interface AgentPresetPromptDiffProps {
  baseLabel: string
  basePrompt: string | null | undefined
  compareLabel: string
  comparePrompt: string | null | undefined
}

export function AgentPresetPromptDiff({
  baseLabel,
  basePrompt,
  compareLabel,
  comparePrompt,
}: AgentPresetPromptDiffProps) {
  return (
    <DiffViewer
      baseLabel={baseLabel}
      baseValue={basePrompt}
      compareLabel={compareLabel}
      compareValue={comparePrompt}
      emptyMessage="No instructions in either version."
    />
  )
}
