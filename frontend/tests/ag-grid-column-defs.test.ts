import type { EditableCallbackParams } from "ag-grid-community"
import type { TableColumnRead } from "@/client"
import {
  buildBaseColumnDef,
  buildEditableColumnDef,
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

describe("buildEditableColumnDef", () => {
  const JSON_COLUMN: TableColumnRead = { ...COLUMN, name: "raw", type: "JSONB" }

  it("edits scalar columns inline for every row by default", () => {
    const def = buildEditableColumnDef(COLUMN, {})

    expect(def.editable).toBe(true)
    expect(def.cellEditor).toBeDefined()
    expect(def.sortable).toBe(true)
  })

  it("leaves JSON columns to the side panel", () => {
    const def = buildEditableColumnDef(JSON_COLUMN, {})

    expect(def.editable).toBe(false)
    expect(def.cellEditor).toBeUndefined()
  })

  it("asks canEditRow per row and hands it to the renderer", () => {
    const canEditRow = (row: unknown) =>
      (row as { locked?: boolean }).locked !== true
    const def = buildEditableColumnDef(COLUMN, {}, { canEditRow })

    expect(typeof def.editable).toBe("function")
    const editable = def.editable as (params: EditableCallbackParams) => boolean
    expect(
      editable({ data: { locked: false } } as EditableCallbackParams)
    ).toBe(true)
    expect(editable({ data: { locked: true } } as EditableCallbackParams)).toBe(
      false
    )
    expect(def.cellRendererParams.canEditRow).toBe(canEditRow)
  })
})
