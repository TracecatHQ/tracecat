"use client"

import { useEffect, useState } from "react"

/**
 * Subscribes to a CSS media query and returns whether it currently matches.
 *
 * State is lazily initialized from `window.matchMedia` so the first client
 * render already reflects the real viewport (no flash of the wrong layout).
 * On the server it initializes to `false`.
 *
 * @param query - A media query string, e.g. `"(max-width: 1279px)"`.
 * @returns `true` while the query matches.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined") {
      return false
    }
    return window.matchMedia(query).matches
  })

  useEffect(() => {
    const mediaQueryList = window.matchMedia(query)

    function handleChange(event: MediaQueryListEvent) {
      setMatches(event.matches)
    }

    // Re-sync in case the viewport changed between render and subscription.
    setMatches(mediaQueryList.matches)
    mediaQueryList.addEventListener("change", handleChange)
    return () => {
      mediaQueryList.removeEventListener("change", handleChange)
    }
  }, [query])

  return matches
}
