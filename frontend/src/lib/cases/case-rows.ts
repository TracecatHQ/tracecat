import type { RowClassRules } from "ag-grid-community"
import type { CaseTableRowRead, TableRowRead } from "@/client"

/**
 * Sentinel field marking a link whose source table row was deleted. The
 * backend reserves the `__tc_` prefix for internal columns and rejects user
 * columns that start with it, so this key can never collide with a table
 * column.
 */
export const UNAVAILABLE_ROW_FLAG = "__tc_unavailable"

/**
 * Dims links whose source row is gone. They stay selectable so they can be
 * unlinked.
 */
export const UNAVAILABLE_ROW_CLASS_RULES: RowClassRules<TableRowRead> = {
  "opacity-50": (params) => params.data?.[UNAVAILABLE_ROW_FLAG] === true,
}

/** Flatten a case-row link into the grid row shape the table's columns expect. */
export function toGridRow(link: CaseTableRowRead): TableRowRead {
  const payload =
    link.row_data && typeof link.row_data === "object" ? link.row_data : {}
  return {
    created_at: link.created_at,
    updated_at: link.updated_at,
    ...payload,
    id: link.row_id,
    [UNAVAILABLE_ROW_FLAG]: link.is_row_available === false || !link.row_data,
  }
}
