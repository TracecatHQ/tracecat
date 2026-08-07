/**
 * @jest-environment jsdom
 */

import {
  type CaretGeometry,
  getTextareaCaretCoordinates,
  resolveCaretCoordinates,
} from "@/lib/textarea-caret"

function createGeometry(overrides: Partial<CaretGeometry> = {}): CaretGeometry {
  return {
    markerTop: 0,
    markerLeft: 0,
    borderTopWidth: 0,
    borderLeftWidth: 0,
    scrollTop: 0,
    scrollLeft: 0,
    lineHeight: 20,
    ...overrides,
  }
}

describe("resolveCaretCoordinates", () => {
  it("adds the textarea border back to the mirror measurements", () => {
    expect(
      resolveCaretCoordinates(
        createGeometry({
          markerTop: 40,
          markerLeft: 12,
          borderTopWidth: 1,
          borderLeftWidth: 2,
        })
      )
    ).toEqual({ top: 41, left: 14, height: 20 })
  })

  it("subtracts scroll offsets so the caret follows the visible text", () => {
    expect(
      resolveCaretCoordinates(
        createGeometry({
          markerTop: 120,
          markerLeft: 30,
          scrollTop: 100,
          scrollLeft: 10,
        })
      )
    ).toEqual({ top: 20, left: 20, height: 20 })
  })

  it("passes the line height through as the caret height", () => {
    expect(
      resolveCaretCoordinates(createGeometry({ lineHeight: 24 })).height
    ).toBe(24)
  })
})

describe("getTextareaCaretCoordinates", () => {
  function createTextarea(value: string, style: string): HTMLTextAreaElement {
    const textarea = document.createElement("textarea")
    textarea.value = value
    textarea.setAttribute("style", style)
    document.body.appendChild(textarea)
    return textarea
  }

  afterEach(() => {
    document.body.innerHTML = ""
  })

  it("returns finite coordinates for a controlled style fixture", () => {
    const textarea = createTextarea(
      "hello @tri",
      "width: 200px; font-size: 12px; line-height: 18px; border-width: 0px; padding: 0px"
    )

    const coordinates = getTextareaCaretCoordinates(textarea, 10)

    expect(Number.isFinite(coordinates.top)).toBe(true)
    expect(Number.isFinite(coordinates.left)).toBe(true)
    expect(coordinates.height).toBe(18)
  })

  it("falls back to a font-derived height when line-height does not parse", () => {
    const textarea = createTextarea(
      "hello",
      "width: 200px; font-size: 10px; line-height: normal"
    )

    expect(getTextareaCaretCoordinates(textarea, 5).height).toBe(12)
  })

  it("removes the measurement mirror from the document", () => {
    const textarea = createTextarea("hello", "width: 200px")

    getTextareaCaretCoordinates(textarea, 5)

    expect(document.body.children).toHaveLength(1)
    expect(document.body.firstElementChild).toBe(textarea)
  })
})
