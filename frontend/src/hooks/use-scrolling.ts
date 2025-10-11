import type { RefObject } from "react"
import { useEffect, useState } from "react"

type ScrollTarget = RefObject<HTMLElement> | Window | null | undefined
type EventTargetWithScroll = Window | HTMLElement | Document

interface UseScrollingOptions {
  debounce?: number
  fallbackToDocument?: boolean
}

export function useScrolling(
  target?: ScrollTarget,
  options: UseScrollingOptions = {}
): boolean {
  const { debounce = 150, fallbackToDocument = true } = options
  const [isScrolling, setIsScrolling] = useState(false)

  useEffect(() => {
    // Resolve element or window
    const element: EventTargetWithScroll =
      target && typeof Window !== "undefined" && target instanceof Window
        ? target
        : ((target as RefObject<HTMLElement>)?.current ?? window)

    // Mobile: fallback to document when using window
    const eventTarget: EventTargetWithScroll =
      fallbackToDocument &&
      element === window &&
      typeof document !== "undefined"
        ? document
        : element

    const on = (
      el: EventTargetWithScroll,
      event: string,
      handler: EventListener
    ) => el.addEventListener(event, handler, { passive: true })

    const off = (
      el: EventTargetWithScroll,
      event: string,
      handler: EventListener
    ) => el.removeEventListener(event, handler)

    let timeout: ReturnType<typeof setTimeout>
    const supportsScrollEnd = element === window && "onscrollend" in window

    const handleScroll: EventListener = () => {
      if (!isScrolling) setIsScrolling(true)

      if (!supportsScrollEnd) {
        clearTimeout(timeout)
        timeout = setTimeout(() => setIsScrolling(false), debounce)
      }
    }

    const handleScrollEnd: EventListener = () => setIsScrolling(false)

    on(eventTarget, "scroll", handleScroll)
    if (supportsScrollEnd) {
      on(eventTarget, "scrollend", handleScrollEnd)
    }

    return () => {
      off(eventTarget, "scroll", handleScroll)
      if (supportsScrollEnd) {
        off(eventTarget, "scrollend", handleScrollEnd)
      }
      clearTimeout(timeout)
    }
  }, [target, debounce, fallbackToDocument, isScrolling])

  return isScrolling
}
