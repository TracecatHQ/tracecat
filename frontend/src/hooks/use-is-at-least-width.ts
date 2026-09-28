"use client"

import { useEffect, useState } from "react"

/**
 * Observes an element with `ResizeObserver` and reports whether its width is
 * at least `minWidth` CSS pixels.
 *
 * Stores a single boolean so consumers only re-render when the answer
 * actually flips — unlike `useElementRect`, which attaches window
 * scroll/resize listeners and produces a fresh rect object on every tick.
 * Initial state is `true` so wide layouts render docked on first paint with
 * no stacked-to-docked flash; zero-width measurements (unmeasured or hidden
 * elements) are ignored rather than treated as narrow.
 *
 * Takes the element itself rather than a `RefObject` so callers can pass a
 * `useState` setter as a callback ref. A ref object would leave the observer
 * permanently unattached whenever the measured node mounts later than the
 * hook — for example behind a loading or error early return — because
 * `ref.current` is null on the first effect run and the deps never change.
 *
 * @param element - The element to measure, or `null` before it mounts.
 * @param minWidth - Minimum width in CSS pixels.
 * @returns `true` while the element is at least `minWidth` wide.
 */
export function useIsAtLeastWidth(
  element: Element | null,
  minWidth: number
): boolean {
  const [isAtLeast, setIsAtLeast] = useState(true)

  useEffect(() => {
    if (!element || typeof ResizeObserver === "undefined") {
      return
    }

    const observer = new ResizeObserver((entries) => {
      const width = entries[entries.length - 1]?.contentRect.width
      // Skip unmeasured/hidden passes instead of flashing the narrow layout.
      if (!width) {
        return
      }
      setIsAtLeast(width >= minWidth)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [element, minWidth])

  return isAtLeast
}
