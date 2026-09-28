import type { TableColumnRead } from "@/client"
import {
  buildBaseColumnDef,
  buildReadOnlyColumnDefs,
} from "@/components/tables/ag-grid-column-defs"

const COLUMN: TableColumnRead = {
  id: "col-1",
  name: "title",
  type: "TEXT",
  nullable: true,
  default: null,
  options: null,
  is_index: false,
}

describe("buildReadOnlyColumnDefs", () => {
  it("disables header sorting because the grid only ever holds one page", () => {
    const [def] = buildReadOnlyColumnDefs([COLUMN], {})

    expect(def.field).toBe("title")
    expect(def.editable).toBe(false)
    expect(def.sortable).toBe(false)
  })

  it("keeps the base def sortable for the editable grid", () => {
    expect(buildBaseColumnDef(COLUMN, {}).sortable).toBe(true)
  })

  it("prefers a persisted width over the type default", () => {
    const [def] = buildReadOnlyColumnDefs([COLUMN], { title: 240 })

    expect(def.width).toBe(240)
  })
})
