"use client"

import { useEffect, useRef } from "react"
import { useSidebar } from "@/components/ui/sidebar"
import { useMediaQuery } from "@/hooks/use-media-query"

/** Viewport width in pixels below which the left nav auto-collapses. */
const AUTO_COLLAPSE_MAX_WIDTH = 1280

/**
 * Auto-collapses the left nav when the viewport narrows below
 * {@link AUTO_COLLAPSE_MAX_WIDTH} and restores it when the viewport widens
 * again — but only if this hook was the one that collapsed it.
 *
 * The effect reacts exclusively to threshold *crossings*, never to the
 * current `open` value, which yields these semantics:
 *
 * - A manual re-open while narrow is not immediately undone.
 * - A nav the user collapsed by hand is never auto-restored: `open` is
 *   already `false` at the narrowing crossing, so the auto-collapse flag
 *   never latches.
 *
 * Both automatic transitions call `setOpen(..., { persist: false })` so they
 * never overwrite the user's saved sidebar preference cookie.
 *
 * Must be called inside a `SidebarProvider`.
 */
export function useAutoCollapseSidebar() {
  const { open, setOpen } = useSidebar()
  const isNarrow = useMediaQuery(
    `(max-width: ${AUTO_COLLAPSE_MAX_WIDTH - 1}px)`
  )
  // null = first run, so mounting while already narrow still collapses.
  const wasNarrow = useRef<boolean | null>(null)
  const autoCollapsed = useRef(false)
  const openRef = useRef(open)
  openRef.current = open

  useEffect(() => {
    // `setOpen`'s identity changes whenever `open` does; this guard absorbs
    // those re-runs so only genuine threshold crossings act.
    if (wasNarrow.current === isNarrow) {
      return
    }
    wasNarrow.current = isNarrow
    if (isNarrow) {
      if (openRef.current) {
        autoCollapsed.current = true
        setOpen(false, { persist: false })
      }
      return
    }
    if (autoCollapsed.current) {
      autoCollapsed.current = false
      setOpen(true, { persist: false })
    }
  }, [isNarrow, setOpen])
}
