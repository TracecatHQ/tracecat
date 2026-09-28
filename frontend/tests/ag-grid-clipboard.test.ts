/**
 * @jest-environment jsdom
 */

import type { GridApi } from "ag-grid-community"
import type React from "react"
import { handleGridKeyDown } from "@/components/tables/ag-grid-clipboard"

function createRowNode() {
  return { data: { name: "before" }, setDataValue: jest.fn() }
}

function createGridApi(rowNode: ReturnType<typeof createRowNode>) {
  return {
    getEditingCells: () => [],
    getSelectedRows: () => [],
    getFocusedCell: () => ({ rowIndex: 0, column: { getColId: () => "name" } }),
    getDisplayedRowAtIndex: () => rowNode,
    getColumns: () => null,
  } as unknown as GridApi
}

function createKeyEvent(key: string) {
  return {
    key,
    ctrlKey: true,
    metaKey: false,
    preventDefault: jest.fn(),
  } as unknown as React.KeyboardEvent
}

describe("handleGridKeyDown", () => {
  const originalClipboard = navigator.clipboard
  let clipboard: { writeText: jest.Mock; readText: jest.Mock }

  beforeEach(() => {
    clipboard = {
      writeText: jest.fn(),
      readText: jest.fn().mockResolvedValue("pasted"),
    }
    Object.defineProperty(navigator, "clipboard", {
      value: clipboard,
      configurable: true,
    })
  })

  afterEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      configurable: true,
    })
  })

  it("copies the focused cell even when read-only", () => {
    const rowNode = createRowNode()
    const event = createKeyEvent("c")

    handleGridKeyDown(event, createGridApi(rowNode), { readOnly: true })

    expect(clipboard.writeText).toHaveBeenCalledWith("before")
    expect(event.preventDefault).toHaveBeenCalled()
  })

  it("pastes into the focused cell by default", async () => {
    const rowNode = createRowNode()
    const event = createKeyEvent("v")

    handleGridKeyDown(event, createGridApi(rowNode))
    await Promise.resolve()
    await Promise.resolve()

    expect(clipboard.readText).toHaveBeenCalled()
    expect(rowNode.setDataValue).toHaveBeenCalledWith("name", "pasted")
    expect(event.preventDefault).toHaveBeenCalled()
  })

  it("ignores paste when read-only", async () => {
    const rowNode = createRowNode()
    const event = createKeyEvent("v")

    handleGridKeyDown(event, createGridApi(rowNode), { readOnly: true })
    await Promise.resolve()
    await Promise.resolve()

    expect(clipboard.readText).not.toHaveBeenCalled()
    expect(rowNode.setDataValue).not.toHaveBeenCalled()
    expect(event.preventDefault).not.toHaveBeenCalled()
  })

  it("does nothing without a grid api", () => {
    const copyEvent = createKeyEvent("c")
    const pasteEvent = createKeyEvent("v")

    handleGridKeyDown(copyEvent, null)
    handleGridKeyDown(pasteEvent, null)

    expect(clipboard.writeText).not.toHaveBeenCalled()
    expect(clipboard.readText).not.toHaveBeenCalled()
    expect(copyEvent.preventDefault).not.toHaveBeenCalled()
    expect(pasteEvent.preventDefault).not.toHaveBeenCalled()
  })
})
