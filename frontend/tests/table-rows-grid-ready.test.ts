/**
 * @jest-environment jsdom
 */

// AG Grid's own modules are not needed to test the readiness check.
jest.mock("@/components/tables/ag-grid-setup", () => ({}))
jest.mock("ag-grid-react", () => ({ AgGridReact: () => null }))

import { isReadyToMeasure } from "@/components/tables/table-rows-grid"

/** A grid wrapper with `rows` rows of `cells`, where a cell is its inner HTML. */
function buildWrapper(rows: string[][], width = 754): HTMLElement {
  const wrapper = document.createElement("div")
  Object.defineProperty(wrapper, "clientWidth", { value: width })
  const container = document.createElement("div")
  container.className = "ag-center-cols-container"
  for (const cells of rows) {
    const row = document.createElement("div")
    row.className = "ag-row"
    for (const html of cells) {
      const cell = document.createElement("div")
      cell.className = "ag-cell"
      cell.innerHTML = html
      row.appendChild(cell)
    }
    container.appendChild(row)
  }
  wrapper.appendChild(container)
  return wrapper
}

const FILLED = "<div>value</div>"

describe("isReadyToMeasure", () => {
  it("is ready once every expected row has its cells filled in", () => {
    const wrapper = buildWrapper([
      [FILLED, FILLED],
      [FILLED, FILLED],
    ])

    expect(isReadyToMeasure({ wrapper, isLoading: false, rowCount: 2 })).toBe(
      true
    )
  })

  it("waits while a load is in flight", () => {
    const wrapper = buildWrapper([])

    expect(isReadyToMeasure({ wrapper, isLoading: true, rowCount: 0 })).toBe(
      false
    )
  })

  it("waits for a grid that has no width yet", () => {
    const wrapper = buildWrapper([[FILLED]], 0)

    expect(isReadyToMeasure({ wrapper, isLoading: false, rowCount: 1 })).toBe(
      false
    )
    expect(
      isReadyToMeasure({ wrapper: null, isLoading: false, rowCount: 1 })
    ).toBe(false)
  })

  it("waits for rows that are not on screen yet", () => {
    const wrapper = buildWrapper([[FILLED]])

    expect(isReadyToMeasure({ wrapper, isLoading: false, rowCount: 4 })).toBe(
      false
    )
  })

  it("waits for cells whose renderer has not landed", () => {
    const wrapper = buildWrapper([
      [FILLED, FILLED],
      [FILLED, ""],
    ])

    expect(isReadyToMeasure({ wrapper, isLoading: false, rowCount: 2 })).toBe(
      false
    )
  })

  it("measures the headers of a table with no rows", () => {
    const wrapper = buildWrapper([])

    expect(isReadyToMeasure({ wrapper, isLoading: false, rowCount: 0 })).toBe(
      true
    )
  })
})
