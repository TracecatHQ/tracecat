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
  const hasRequiredFields =
    name.trim().length > 0 &&
    modelProvider.trim().length > 0 &&
    modelName.trim().length > 0

  if (mode === "edit") {
    return isDirty && hasRequiredFields
  }

  return hasRequiredFields
}
