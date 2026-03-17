import { getModelSelectionKey } from "@/lib/hooks"

const AI_ACTIONS_WITH_COMPOSITE_MODEL = new Set([
  "ai.agent",
  "ai.action",
  "ai.rank_documents",
  "ai.select_field",
  "ai.select_fields",
])

export function normalizeRegistryActionInputs(
  actionType: string | null | undefined,
  inputs: Record<string, unknown>
): Record<string, unknown> {
  if (!actionType || !AI_ACTIONS_WITH_COMPOSITE_MODEL.has(actionType)) {
    return inputs
  }
  if (typeof inputs.model === "string") {
    return inputs
  }
  if (
    typeof inputs.model_name !== "string" ||
    typeof inputs.model_provider !== "string"
  ) {
    return inputs
  }

  const normalized: Record<string, unknown> = {
    ...inputs,
    model: getModelSelectionKey({
      source_id: typeof inputs.source_id === "string" ? inputs.source_id : null,
      model_provider: inputs.model_provider,
      model_name: inputs.model_name,
    }),
  }

  delete normalized.model_name
  delete normalized.model_provider
  delete normalized.source_id

  return normalized
}
