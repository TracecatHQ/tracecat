export type AgentPresetFormMode = "create" | "edit"

export function canSubmitAgentPresetForm({
  mode,
  isDirty,
  name,
  modelProvider,
  modelName,
}: {
  mode: AgentPresetFormMode
  isDirty: boolean
  name: string
  modelProvider: string
  modelName: string
}) {
  if (mode === "edit") {
    return isDirty
  }

  return Boolean(
    name.trim().length > 0 &&
      modelProvider.trim().length > 0 &&
      modelName.trim().length > 0
  )
}
