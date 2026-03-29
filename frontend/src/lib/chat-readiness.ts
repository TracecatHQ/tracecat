import type { ModelSelection } from "@/client"

type ReadinessPresetLike = {
  source_id?: string | null
  model_provider?: string | null
  model_name?: string | null
}

export function buildChatReadinessOptions({
  workspaceId,
  preset,
  selection,
}: {
  workspaceId: string
  preset?: ReadinessPresetLike | null
  selection?: ModelSelection | null
}) {
  if (preset?.model_provider && preset.model_name) {
    return {
      workspaceId,
      selection: {
        source_id: preset.source_id ?? null,
        model_provider: preset.model_provider,
        model_name: preset.model_name,
      },
    }
  }

  if (selection) {
    return {
      workspaceId,
      selection,
    }
  }

  return { workspaceId }
}
