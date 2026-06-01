"use client"

import { useEffect, useRef, useState } from "react"
import { Response } from "@/components/ai-elements/response"

/** Minimum characters revealed per animation frame while catching up. */
const MIN_CHARS_PER_FRAME = 3
/** Fraction of the outstanding gap revealed each frame (eases out bursts). */
const CATCHUP_FRACTION = 0.25

interface SmoothResponseProps {
  text: string
  /**
   * When true, newly-arrived characters are revealed on
   * `requestAnimationFrame` instead of appearing in the SDK's coarse throttle
   * steps. When false the full text renders immediately.
   */
  animate: boolean
}

/**
 * Renders streamed markdown with a frame-aligned reveal.
 *
 * Streamed text can arrive in uneven network-sized chunks. While `animate` is
 * true this component advances a locally-held reveal length on each animation
 * frame, so bursty deltas read as smooth 60fps growth. The reveal state is
 * local, so per-frame updates only re-render this component, not the
 * surrounding message list.
 */
export function SmoothResponse({ text, animate }: SmoothResponseProps) {
  const [shownLen, setShownLen] = useState(text.length)
  // Track the latest target length without restarting the RAF loop on every
  // text update.
  const targetLenRef = useRef(text.length)
  targetLenRef.current = text.length

  useEffect(() => {
    if (!animate) {
      // Snap to the full text when the stream settles.
      setShownLen(targetLenRef.current)
      return
    }

    let rafId = requestAnimationFrame(function tick() {
      setShownLen((prev) => {
        const target = targetLenRef.current
        if (prev >= target) {
          // Caught up: identical state value, React skips the re-render.
          return prev
        }
        const step = Math.max(
          MIN_CHARS_PER_FRAME,
          Math.ceil((target - prev) * CATCHUP_FRACTION)
        )
        return Math.min(target, prev + step)
      })
      rafId = requestAnimationFrame(tick)
    })

    return () => cancelAnimationFrame(rafId)
  }, [animate])

  const shown = animate ? text.slice(0, shownLen) : text
  return <Response>{shown}</Response>
}
