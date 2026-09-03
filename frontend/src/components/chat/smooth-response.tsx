"use client"

import { Response } from "@/components/ai-elements/response"
import { useSmoothText } from "@/hooks/use-smooth-text"

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
 * true the incoming text is revealed at a steady frame-aligned rate via
 * {@link useSmoothText}, avoiding the "catch up, pause, catch up" pulse that
 * happens when each network burst is revealed as fast as possible.
 */
export function SmoothResponse({ text, animate }: SmoothResponseProps) {
  const shown = useSmoothText(text, animate)
  return <Response>{shown}</Response>
}
