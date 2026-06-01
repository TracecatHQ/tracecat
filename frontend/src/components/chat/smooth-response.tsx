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
  const animateRef = useRef(animate)
  animateRef.current = animate
  // Track the latest target length without restarting the RAF loop on every
  // text update.
  const targetLenRef = useRef(text.length)
  targetLenRef.current = text.length
  const shownLenRef = useRef(shownLen)
  shownLenRef.current = shownLen
  const rafIdRef = useRef<number | null>(null)
  const lastFrameAtRef = useRef(0)
  const carryRef = useRef(0)

  useEffect(() => {
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current)
      }
    }
  }, [])

  useEffect(() => {
    function stopRevealLoop() {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current)
        rafIdRef.current = null
      }
      carryRef.current = 0
    }

    function syncShownLen(nextShownLen: number) {
      if (shownLenRef.current === nextShownLen) {
        return
      }
      shownLenRef.current = nextShownLen
      setShownLen(nextShownLen)
    }

    if (!animate) {
      // Snap to the full text when the stream settles.
      stopRevealLoop()
      syncShownLen(targetLenRef.current)
      return
    }

    const targetLen = targetLenRef.current
    if (shownLenRef.current > targetLen) {
      carryRef.current = 0
      syncShownLen(targetLen)
      return
    }
    if (shownLenRef.current >= targetLen || rafIdRef.current !== null) {
      carryRef.current = 0
      return
    }

    lastFrameAtRef.current = performance.now()
    rafIdRef.current = requestAnimationFrame(function tick(now) {
      rafIdRef.current = null
      if (!animateRef.current) {
        carryRef.current = 0
        return
      }

      const elapsedSeconds = Math.min(
        (now - lastFrameAtRef.current) / 1000,
        0.1
      )
      lastFrameAtRef.current = now

      const prev = shownLenRef.current
      const target = targetLenRef.current
      let next = prev

      if (prev > target) {
        carryRef.current = 0
        next = target
      } else if (prev < target) {
        const buffered = target - prev
        const charsPerSecond =
          buffered > CATCHUP_THRESHOLD_CHARS
            ? CATCHUP_CHARS_PER_SECOND
            : STEADY_CHARS_PER_SECOND
        const exactStep = charsPerSecond * elapsedSeconds + carryRef.current
        const step = Math.min(
          Math.floor(exactStep),
          MAX_CHARS_PER_FRAME,
          buffered
        )
        carryRef.current = exactStep - Math.floor(exactStep)
        if (step < 1) {
          next = prev
        } else {
          next = Math.min(target, prev + step)
        }
      } else {
        carryRef.current = 0
      }

      syncShownLen(next)
      if (next < targetLenRef.current) {
        rafIdRef.current = requestAnimationFrame(tick)
      } else {
        carryRef.current = 0
      }
    })
  }, [animate, text.length])

  const shown = animate ? text.slice(0, shownLen) : text
  return <Response>{shown}</Response>
}
