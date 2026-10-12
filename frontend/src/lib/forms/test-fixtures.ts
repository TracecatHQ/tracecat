import type { FormFieldBlock, FormResolved } from "@/client"

/** Build a field block for tests. Defaults to an optional text field. */
export function fieldBlock(
  overrides: Partial<FormFieldBlock> & Pick<FormFieldBlock, "name">
): FormFieldBlock {
  return {
    type: "field",
    label: overrides.name,
    widget: "text",
    required: false,
    multiple: false,
    ...overrides,
  }
}

/** Build a resolved form for tests. */
export function resolvedForm(
  overrides: Partial<FormResolved> = {}
): FormResolved {
  return {
    id: "form-1",
    workspace_id: "workspace-1",
    workflow_id: "wf-1",
    status: "online",
    title: "Laptop request",
    definition_version: 4,
    blocks: [],
    ...overrides,
  }
}
