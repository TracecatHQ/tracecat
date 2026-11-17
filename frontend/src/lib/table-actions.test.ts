import { getTableActionRef } from "@/lib/table-actions"

describe("getTableActionRef", () => {
  it("reads the table name for core.table row actions", () => {
    expect(
      getTableActionRef("core.table.lookup", { table: "alerts", column: "id" })
    ).toEqual({ inputKey: "table", by: "name", value: "alerts" })
  })

  it("reads the name input for table-level actions", () => {
    expect(
      getTableActionRef("core.table.get_table_metadata", { name: "alerts" })
    ).toEqual({ inputKey: "name", by: "name", value: "alerts" })
  })

  it("reads table_id for case row link actions", () => {
    expect(
      getTableActionRef("core.cases.link_row", { table_id: "abc", row_id: "1" })
    ).toEqual({ inputKey: "table_id", by: "id", value: "abc" })
  })

  it("leaves the value unset when the input is missing or not a string", () => {
    expect(getTableActionRef("core.table.insert_row", {})).toEqual({
      inputKey: "table",
      by: "name",
      value: undefined,
    })
    expect(
      getTableActionRef("core.table.insert_row", { table: { a: 1 } })?.value
    ).toBeUndefined()
  })

  it("ignores actions without a target table", () => {
    expect(getTableActionRef("core.table.create_table", { name: "x" })).toBe(
      null
    )
    expect(getTableActionRef("core.table.list_tables", {})).toBe(null)
    expect(getTableActionRef("core.cases.create_case", {})).toBe(null)
    expect(getTableActionRef("core.http_request", { table: "x" })).toBe(null)
    expect(getTableActionRef(undefined, {})).toBe(null)
  })
})
