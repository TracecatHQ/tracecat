const TABLE_NAME_INPUT_ACTIONS: Record<string, string> = {
  "core.table.get_table_metadata": "name",
  "core.table.update_table": "name",
  "core.table.download": "name",
}
const TABLE_ACTIONS_WITHOUT_TABLE_INPUT = new Set([
  "core.table.create_table",
  "core.table.list_tables",
])
const TABLE_ID_INPUT_ACTIONS = new Set([
  "core.cases.link_row",
  "core.cases.unlink_row",
  "core.cases.insert_row",
  "core.cases.list_cases_by_row",
])

export type TableActionRef = {
  inputKey: string
  by: "name" | "id"
  value?: string
}

/** Returns the input that identifies the target table of a table action. */
export function getTableActionRef(
  actionType: string | undefined,
  inputs: Record<string, unknown> | undefined
): TableActionRef | null {
  if (!actionType) {
    return null
  }
  let ref: Omit<TableActionRef, "value">
  if (TABLE_ID_INPUT_ACTIONS.has(actionType)) {
    ref = { inputKey: "table_id", by: "id" }
  } else if (
    actionType.startsWith("core.table.") &&
    !TABLE_ACTIONS_WITHOUT_TABLE_INPUT.has(actionType)
  ) {
    ref = {
      inputKey: TABLE_NAME_INPUT_ACTIONS[actionType] ?? "table",
      by: "name",
    }
  } else {
    return null
  }
  const raw = inputs?.[ref.inputKey]
  const value = typeof raw === "string" && raw.trim() ? raw.trim() : undefined
  return { ...ref, value }
}
