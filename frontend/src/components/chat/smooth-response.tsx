"use client"

import { useEffect, useRef, useState } from "react"
import { Response } from "@/components/ai-elements/response"

/** Normal reveal speed. At 60fps this is roughly 3 characters per frame. */
const STEADY_CHARS_PER_SECOND = 180
/** Catch-up speed used only when the hidden buffer grows large. */
const CATCHUP_CHARS_PER_SECOND = 420
/** Start catching up once the UI is meaningfully behind the source stream. */
const CATCHUP_THRESHOLD_CHARS = 140
/** Prevent a single frame from dumping a large buffered chunk. */
const MAX_CHARS_PER_FRAME = 12

interface SmoothResponseProps {
  text: string
  /**
   * When true, newly-arrived characters are revealed on
   * `requestAnimationFrame` instead of appearing in uneven stream-sized chunks.
   * When false the full text renders immediately.
   */
  animate: boolean
}

/**
 * Renders streamed markdown with a frame-aligned reveal.
 *
 * Streamed text can arrive in uneven network-sized chunks. While `animate` is
 * true this component treats incoming text as a hidden buffer and drains it at
 * a steady frame-aligned rate. This avoids the "catch up, pause, catch up"
 * pulse that happens when each network burst is revealed as fast as possible.
 * The reveal state is local, so per-frame updates only re-render this
 * component, not the surrounding message list.
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

    let lastFrameAt = performance.now()
    let carry = 0

    let rafId = requestAnimationFrame(function tick(now) {
      const elapsedSeconds = Math.min((now - lastFrameAt) / 1000, 0.1)
      lastFrameAt = now

      setShownLen((prev) => {
        const target = targetLenRef.current
        if (prev > target) {
          carry = 0
          return target
        }
        if (prev >= target) {
          carry = 0
          return prev
        }

        const buffered = target - prev
        const charsPerSecond =
          buffered > CATCHUP_THRESHOLD_CHARS
            ? CATCHUP_CHARS_PER_SECOND
            : STEADY_CHARS_PER_SECOND
        const exactStep = charsPerSecond * elapsedSeconds + carry
        const step = Math.min(
          Math.floor(exactStep),
          MAX_CHARS_PER_FRAME,
          buffered
        )
        carry = exactStep - Math.floor(exactStep)
        if (step < 1) {
          return prev
        }
        return Math.min(target, prev + step)
      })
      rafId = requestAnimationFrame(tick)
    })

    return () => cancelAnimationFrame(rafId)
  }, [animate])

  const shown = animate ? text.slice(0, shownLen) : text
  return <Response>{shown}</Response>
}
