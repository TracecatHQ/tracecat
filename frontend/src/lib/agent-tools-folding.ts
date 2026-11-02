import { getTracecatComponents, isTracecatJsonSchema } from "@/lib/schema"

type FieldEntry = [fieldName: string, fieldDefn: unknown]

/** Sibling args the unified Tools field of an agent action reads and writes. */
export interface AgentToolsFolding {
  /** The multi-select `action-type` arg that renders as "Tools". */
  actionsField: string | null
  /** The `mcp-integration` arg, when the Tools field can represent its value. */
  mcpField: string | null
  /** The `tool_approvals` arg, when the Tools field can represent its value. */
  approvalsField: string | null
  /** Args that no longer render as fields of their own. */
  foldedFields: Set<string>
}

/** Whether a value is a list of strings. */
export function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
}

function isApprovalMap(value: unknown): value is Record<string, boolean> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.values(value).every((item) => typeof item === "boolean")
  )
}

function findField(
  properties: Record<string, unknown>,
  componentId: "action-type" | "mcp-integration"
): string | null {
  for (const [fieldName, schema] of Object.entries(properties)) {
    if (!isTracecatJsonSchema(schema)) continue
    const found = getTracecatComponents(schema).some((component) => {
      if (component.component_id !== componentId) return false
      // Only the multi-select action picker is the agent's tool list.
      if (component.component_id === "action-type")
        return component.multiple === true
      return true
    })
    if (found) return fieldName
  }
  return null
}

/**
 * Decide which sibling args the Tools field owns, from the action schema and
 * the current inputs. Unset and null values fold; a sibling holding an
 * expression or any other shape the UI cannot represent is left to its own
 * field so it is never overwritten.
 */
export function getAgentToolsFolding(
  properties: Record<string, unknown>,
  inputs: Record<string, unknown>
): AgentToolsFolding {
  const actionsField = findField(properties, "action-type")
  const actionsValue = actionsField ? inputs[actionsField] : undefined
  if (!actionsField || (actionsValue != null && !isStringArray(actionsValue))) {
    return {
      actionsField,
      mcpField: null,
      approvalsField: null,
      foldedFields: new Set(),
    }
  }

  let mcpField = findField(properties, "mcp-integration")
  if (mcpField && inputs[mcpField] != null && !isStringArray(inputs[mcpField]))
    mcpField = null

  let approvalsField = "tool_approvals" in properties ? "tool_approvals" : null
  if (
    approvalsField &&
    inputs[approvalsField] != null &&
    !isApprovalMap(inputs[approvalsField])
  )
    approvalsField = null

  return {
    actionsField,
    mcpField,
    approvalsField,
    foldedFields: new Set(
      [mcpField, approvalsField].filter((field) => field !== null)
    ),
  }
}

/** Label for an action arg: "Tools" for the unified field, else the humanized name. */
export function getActionFieldLabel(
  fieldName: string,
  folding: AgentToolsFolding
): string {
  if (fieldName === folding.actionsField) return "Tools"
  return fieldName.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase())
}

/** Drop args folded into the Tools field from a field list. */
export function withoutFoldedFields<T extends FieldEntry>(
  fields: T[],
  folding: AgentToolsFolding
): T[] {
  return fields.filter(([fieldName]) => !folding.foldedFields.has(fieldName))
}

/**
 * Optional fields to render: those with a value, shown by default, or enabled
 * by the user, minus those the user hid. The Tools field counts as having a
 * value when any arg folded into it has one.
 */
export function getVisibleOptionalFields({
  optionalFields,
  inputs,
  folding,
  manuallyVisible,
  manuallyHidden,
  showByDefault,
}: {
  optionalFields: FieldEntry[]
  inputs: Record<string, unknown>
  folding: AgentToolsFolding
  manuallyVisible: ReadonlySet<string>
  manuallyHidden: ReadonlySet<string>
  showByDefault: (fieldName: string, fieldDefn: unknown) => boolean
}): Set<string> {
  const visible = new Set(manuallyVisible)
  for (const [fieldName, fieldDefn] of optionalFields) {
    if (
      inputs[fieldName] !== undefined ||
      showByDefault(fieldName, fieldDefn)
    ) {
      // A folded arg with a value shows the Tools field that owns it.
      const owner =
        folding.actionsField && folding.foldedFields.has(fieldName)
          ? folding.actionsField
          : fieldName
      visible.add(owner)
    }
  }
  for (const fieldName of manuallyHidden) visible.delete(fieldName)
  return visible
}

/** Remove a hidden optional arg from the inputs; hiding Tools clears its folded args too. */
export function removeOptionalField(
  inputs: Record<string, unknown>,
  fieldName: string,
  folding: AgentToolsFolding
): Record<string, unknown> {
  const removed = new Set([fieldName])
  if (fieldName === folding.actionsField)
    for (const folded of folding.foldedFields) removed.add(folded)
  return Object.fromEntries(
    Object.entries(inputs).filter(([name]) => !removed.has(name))
  )
}
