/**
 * @jest-environment jsdom
 */

import {
  isUserSelectionSource,
  reconcileSelection,
} from "@/components/tables/ag-grid-selection"

describe("reconcileSelection", () => {
  it("keeps ids selected on other pages", () => {
    expect(
      reconcileSelection({
        previous: new Set(["a", "b"]),
        visibleIds: ["b", "c"],
        selectedVisibleIds: ["c"],
      })
    ).toEqual(["a", "c"])
  })

  it("leaves off-page ids when the last visible id is deselected", () => {
    expect(
      reconcileSelection({
        previous: new Set(["a", "b"]),
        visibleIds: ["b"],
        selectedVisibleIds: [],
      })
    ).toEqual(["a"])
  })

  it("unions the whole page on select all", () => {
    expect(
      reconcileSelection({
        previous: new Set(["a"]),
        visibleIds: ["b", "c"],
        selectedVisibleIds: ["b", "c"],
      })
    ).toEqual(["a", "b", "c"])
  })

  it("preserves order and does not duplicate an already-selected visible id", () => {
    expect(
      reconcileSelection({
        previous: new Set(["a", "b", "c"]),
        visibleIds: ["b", "c", "d"],
        selectedVisibleIds: ["b", "c", "d"],
      })
    ).toEqual(["a", "b", "c", "d"])
  })

  it("returns the previous selection in order when nothing is visible", () => {
    expect(
      reconcileSelection({
        previous: new Set(["a", "b", "c"]),
        visibleIds: [],
        selectedVisibleIds: [],
      })
    ).toEqual(["a", "b", "c"])
  })
})

describe("isUserSelectionSource", () => {
  it("accepts user-driven sources", () => {
    expect(isUserSelectionSource("checkboxSelected")).toBe(true)
    expect(isUserSelectionSource("uiSelectAll")).toBe(true)
    expect(isUserSelectionSource("spaceKey")).toBe(true)
    expect(isUserSelectionSource("keyboardSelectAll")).toBe(true)
  })

  it("rejects programmatic sources", () => {
    expect(isUserSelectionSource("api")).toBe(false)
    expect(isUserSelectionSource("rowDataChanged")).toBe(false)
    expect(isUserSelectionSource("gridInitializing")).toBe(false)
    expect(isUserSelectionSource("apiSelectAll")).toBe(false)
  })
})
