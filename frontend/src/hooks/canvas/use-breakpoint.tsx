import { useMemo } from "react"
import { ReactFlowState, useStore } from "@xyflow/react"

// Selector to get the current zoom level from the store with a custom equality function
// to only trigger updates when the zoom level crosses a threshold boundary

type ZoomBreakpoint = "large" | "medium" | "small"

function zoomBreakpointSelector(state: ReactFlowState): ZoomBreakpoint {
  const zoom = state.transform[2]
  if (zoom <= 0.25) {
    return "large"
  } else if (zoom <= 0.5) {
    return "medium"
  } else {
    return "small"
  }
}

export function useActionNodeZoomBreakpoint() {
  const breakpoint = useStore(zoomBreakpointSelector)
  const style = useMemo(() => {
    if (breakpoint === "large") {
      return { fontSize: "text-3xl", showContent: false }
    } else if (breakpoint === "medium") {
      return { fontSize: "text-xl", showContent: false }
    } else {
      return { fontSize: "text-sm", showContent: true }
    }
  }, [breakpoint])
  return {
    breakpoint,
    style,
  }
}

export function useTriggerNodeZoomBreakpoint() {
  const breakpoint = useStore(zoomBreakpointSelector)
  const style = useMemo(() => {
    if (breakpoint === "large") {
      return { fontSize: "text-3xl", showContent: false }
    } else if (breakpoint === "medium") {
      return { fontSize: "text-xl", showContent: false }
    } else {
      return { fontSize: "text-sm", showContent: true }
    }
  }, [breakpoint])
  return {
    breakpoint,
    style,
  }
}
