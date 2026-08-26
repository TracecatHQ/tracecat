/**
 * @jest-environment jsdom
 */

import type { RowClassParams } from "ag-grid-community"
import type { CaseTableRowRead, TableRowRead } from "@/client"
import {
  toGridRow,
  UNAVAILABLE_ROW_CLASS_RULES,
  UNAVAILABLE_ROW_FLAG,
} from "@/lib/cases/case-rows"

function makeLink(overrides: Partial<CaseTableRowRead> = {}): CaseTableRowRead {
  return {
    id: "link-1",
    case_id: "case-1",
    table_id: "table-1",
    table_name: "Table",
    row_id: "row-1",
    row_data: { name: "alice" },
    is_row_available: true,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-02T00:00:00Z",
    ...overrides,
  }
}

function isUnavailable(data: TableRowRead | undefined): boolean {
  const rule = UNAVAILABLE_ROW_CLASS_RULES["opacity-50"]
  if (typeof rule !== "function") {
    throw new Error("Expected a callback rule")
  }
  return rule({ data } as RowClassParams<TableRowRead>)
}

describe("toGridRow", () => {
  it("flattens row_data onto the grid row", () => {
    const row = toGridRow(makeLink({ row_data: { name: "alice", score: 3 } }))
    expect(row.name).toBe("alice")
    expect(row.score).toBe(3)
  })

  it("takes id from row_id even when row_data carries its own id", () => {
    const row = toGridRow(
      makeLink({ row_id: "row-1", row_data: { id: "other-id" } })
    )
    expect(row.id).toBe("row-1")
  })

  it("prefers row timestamps over link timestamps when present", () => {
    const row = toGridRow(
      makeLink({
        row_data: {
          created_at: "2023-05-05T00:00:00Z",
          updated_at: "2023-06-06T00:00:00Z",
        },
      })
    )
    expect(row.created_at).toBe("2023-05-05T00:00:00Z")
    expect(row.updated_at).toBe("2023-06-06T00:00:00Z")
  })

  it("falls back to link timestamps when the row has none", () => {
    const row = toGridRow(makeLink({ row_data: { name: "alice" } }))
    expect(row.created_at).toBe("2024-01-01T00:00:00Z")
    expect(row.updated_at).toBe("2024-01-02T00:00:00Z")
  })

  it("flags links whose source row is unavailable", () => {
    expect(
      toGridRow(makeLink({ is_row_available: false }))[UNAVAILABLE_ROW_FLAG]
    ).toBe(true)
    expect(toGridRow(makeLink({ row_data: null }))[UNAVAILABLE_ROW_FLAG]).toBe(
      true
    )
  })

  it("does not flag a healthy link", () => {
    expect(toGridRow(makeLink())[UNAVAILABLE_ROW_FLAG]).toBe(false)
  })

  it("uses the backend-reserved internal prefix for the flag", () => {
    expect(UNAVAILABLE_ROW_FLAG.startsWith("__tc_")).toBe(true)
  })

  it("leaves a user column the flag could have shadowed untouched", () => {
    const row = toGridRow(makeLink({ row_data: { __unavailable: "keep me" } }))
    expect(row.__unavailable).toBe("keep me")
    expect(row[UNAVAILABLE_ROW_FLAG]).toBe(false)
  })
})

describe("UNAVAILABLE_ROW_CLASS_RULES", () => {
  it("dims only flagged rows", () => {
    expect(
      isUnavailable(toGridRow(makeLink({ is_row_available: false })))
    ).toBe(true)
    expect(isUnavailable(toGridRow(makeLink()))).toBe(false)
  })

  it("tolerates a row group with no data", () => {
    expect(isUnavailable(undefined)).toBe(false)
  })
})
