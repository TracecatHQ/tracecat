import type { FormFieldBlock, FormPreview } from "@/client"

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

/** Build a rendered form for tests. */
export function resolvedForm(
  overrides: Partial<FormPreview> = {}
): FormPreview {
  return {
    title: "Laptop request",
    definition_version: 4,
    config_version: "config-1",
    blocks: [],
    ...overrides,
  }
}
