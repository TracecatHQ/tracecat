/** Primitive types supported by structured agent output. */
export type AgentOutputPrimitive = "str" | "int" | "float" | "bool"

/** Split a stored output literal into its primitive type and list setting. */
export function parseAgentOutputType(value?: string): {
  type: AgentOutputPrimitive | ""
  isList: boolean
} {
  const isList = Boolean(value?.startsWith("list[") && value.endsWith("]"))
  const type = isList ? value?.slice(5, -1) : value
  if (type === "str" || type === "int" || type === "float" || type === "bool") {
    return { type, isList }
  }
  return { type: "", isList: false }
}

/** Combine structured output controls into the persisted output literal. */
export function formatAgentOutputType({
  type,
  isList,
}: {
  type: AgentOutputPrimitive
  isList: boolean
}): AgentOutputPrimitive | `list[${AgentOutputPrimitive}]` {
  return isList ? `list[${type}]` : type
}
