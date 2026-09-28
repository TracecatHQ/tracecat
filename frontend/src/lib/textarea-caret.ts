/**
 * Caret coordinate measurement for plain `<textarea>` elements.
 *
 * Textareas expose no caret geometry, so we mirror the element into a hidden
 * div that copies every style affecting text layout, place a marker span at the
 * caret offset, and read the marker's position back.
 */

/** Caret position relative to the textarea's border box, in pixels. */
export interface CaretCoordinates {
  top: number
  left: number
  /** Height of the caret's line, so callers can anchor above or below it. */
  height: number
}

/** Measurements the mirror produces, kept separate so the math stays pure. */
export interface CaretGeometry {
  markerTop: number
  markerLeft: number
  borderTopWidth: number
  borderLeftWidth: number
  scrollTop: number
  scrollLeft: number
  lineHeight: number
}

/**
 * Convert raw mirror measurements into textarea-relative caret coordinates.
 *
 * The mirror has no border, so the textarea's border widths are added back,
 * and scroll offsets are subtracted to follow the visible text.
 */
export function resolveCaretCoordinates(
  geometry: CaretGeometry
): CaretCoordinates {
  return {
    top: geometry.markerTop + geometry.borderTopWidth - geometry.scrollTop,
    left: geometry.markerLeft + geometry.borderLeftWidth - geometry.scrollLeft,
    height: geometry.lineHeight,
  }
}

/** Styles that must match for the mirror to wrap text exactly as the textarea. */
const MIRRORED_PROPERTIES = [
  "box-sizing",
  "width",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "border-top-width",
  "border-right-width",
  "border-bottom-width",
  "border-left-width",
  "font-family",
  "font-size",
  "font-size-adjust",
  "font-stretch",
  "font-style",
  "font-variant",
  "font-weight",
  "letter-spacing",
  "line-height",
  "tab-size",
  "text-align",
  "text-indent",
  "text-transform",
  "word-spacing",
] as const

function toPixels(value: string, fallback = 0): number {
  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

/**
 * Measure the caret position for `position` within `textarea`.
 *
 * Returns coordinates relative to the textarea's border box. Safe to call on
 * every keystroke; the mirror is created and removed synchronously.
 */
export function getTextareaCaretCoordinates(
  textarea: HTMLTextAreaElement,
  position: number
): CaretCoordinates {
  const computed = window.getComputedStyle(textarea)
  const mirror = document.createElement("div")

  for (const property of MIRRORED_PROPERTIES) {
    mirror.style.setProperty(property, computed.getPropertyValue(property))
  }
  mirror.style.position = "absolute"
  mirror.style.visibility = "hidden"
  mirror.style.whiteSpace = "pre-wrap"
  mirror.style.overflowWrap = "break-word"
  mirror.style.top = "0"
  mirror.style.left = "0"

  mirror.textContent = textarea.value.slice(0, position)

  // A non-empty marker is required for the browser to lay it out at all.
  const marker = document.createElement("span")
  marker.textContent = textarea.value.slice(position) || "."
  mirror.appendChild(marker)

  document.body.appendChild(mirror)
  const fontSize = toPixels(computed.fontSize, 16)
  const geometry: CaretGeometry = {
    markerTop: marker.offsetTop,
    markerLeft: marker.offsetLeft,
    borderTopWidth: toPixels(computed.borderTopWidth),
    borderLeftWidth: toPixels(computed.borderLeftWidth),
    scrollTop: textarea.scrollTop,
    scrollLeft: textarea.scrollLeft,
    // `line-height: normal` does not parse, so approximate it from the font.
    lineHeight: toPixels(computed.lineHeight, Math.round(fontSize * 1.2)),
  }
  document.body.removeChild(mirror)

  return resolveCaretCoordinates(geometry)
}
