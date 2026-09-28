import { type RefObject, useLayoutEffect, useRef, useState } from "react"

interface UseOverflowBadgesOptions {
  /** Horizontal gap between badges, in pixels (e.g. `gap-1` = 4, `gap-1.5` = 6). */
  gap: number
}

interface UseOverflowBadgesResult {
  /** Attach to the hidden measurement container. */
  measureRef: RefObject<HTMLDivElement>
  /** Number of leading badges that fit on a single line. */
  visibleCount: number
}

/**
 * Measures how many badges fit on a single line so a badge strip can render
 * only those that fit plus a trailing "+N" overflow indicator.
 *
 * The consumer renders a hidden measurement layer containing one element per
 * item in order, followed by a final element standing in for the "+N"
 * indicator, and attaches {@link UseOverflowBadgesResult.measureRef} to that
 * layer's container. A `ResizeObserver` recomputes the visible count whenever
 * the container resizes.
 *
 * Shared by `MultiSelectBadges` (case custom fields) and `SelectedToolsHeader`
 * (chat composer); see either for a rendering example.
 */
export function useOverflowBadges<T>(
  items: T[],
  { gap }: UseOverflowBadgesOptions
): UseOverflowBadgesResult {
  const measureRef = useRef<HTMLDivElement>(null)
  const [visibleCount, setVisibleCount] = useState(items.length)

  // useLayoutEffect (not useEffect) so measurement + clamping happen before the
  // browser paints, avoiding a first-render flash where every badge shows at
  // full width for a frame before the visible set is computed.
  useLayoutEffect(() => {
    const container = measureRef.current
    if (!container) {
      return
    }

    const measure = () => {
      const children = Array.from(container.children) as HTMLElement[]
      // Last child stands in for the "+N" indicator.
      const indicatorEl = children[children.length - 1]
      const badges = children.slice(0, -1)
      const indicatorWidth = indicatorEl ? indicatorEl.offsetWidth : 0

      let count = 0
      for (const badge of badges) {
        if (badge.offsetLeft + badge.offsetWidth > container.clientWidth) {
          break
        }
        count++
      }

      // When badges are hidden, reserve room for the "+N" indicator so it does
      // not overlap the last visible badge.
      if (count > 1 && count < badges.length) {
        const lastVisible = badges[count - 1]
        if (
          lastVisible.offsetLeft +
            lastVisible.offsetWidth +
            gap +
            indicatorWidth >
          container.clientWidth
        ) {
          count--
        }
      }

      setVisibleCount(Math.max(count, 1))
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(container)
    return () => observer.disconnect()
  }, [items, gap])

  return { measureRef, visibleCount }
}
