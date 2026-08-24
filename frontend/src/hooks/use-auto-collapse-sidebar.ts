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
 * Only threshold *crossings* collapse or restore the nav, which yields these
 * semantics:
 *
 * - A manual re-open while narrow is not immediately undone.
 * - A nav the user collapsed by hand is never auto-restored: `open` is
 *   already `false` at the narrowing crossing, so the auto-collapse flag
 *   never latches.
 * - A user who re-opens the nav while narrow takes ownership of it, so
 *   whatever they leave it on survives the widening crossing.
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

  useEffect(() => {
    const crossed = wasNarrow.current !== isNarrow
    wasNarrow.current = isNarrow

    if (!crossed) {
      // No crossing, so this run is a plain `open` change (or one of the
      // re-runs `setOpen`'s changing identity causes). While narrow, the hook
      // only ever closes the nav, so an open one is the user's doing and it
      // gives up its claim to restore anything later.
      if (isNarrow && open) {
        autoCollapsed.current = false
      }
      return
    }

    if (isNarrow) {
      if (open) {
        autoCollapsed.current = true
        setOpen(false, { persist: false })
      }
      return
    }

    if (autoCollapsed.current) {
      autoCollapsed.current = false
      setOpen(true, { persist: false })
    }
  }, [isNarrow, open, setOpen])
}
