import { useCallback } from "react"
import { useOrgAppSettings } from "@/lib/hooks"
import { useWorkflowBuilder } from "@/providers/builder"

/** Keyboard shortcut that toggles every `run_if` badge on the canvas. */
export const TOGGLE_CONDITIONS_SHORTCUT = "mod+."

/**
 * Resolve whether the workflow canvas shows every `run_if` condition.
 *
 * The org compact-conditions setting provides the default, and the canvas
 * toggle overrides it for the current builder session.
 */
export function useRunIfDisplay() {
  const { showAllConditions, setShowAllConditions } = useWorkflowBuilder()
  const { appSettings } = useOrgAppSettings()
  const compactByDefault =
    appSettings?.app_workflow_compact_conditions_enabled ?? true
  const showAll = showAllConditions ?? !compactByDefault

  const toggleShowAll = useCallback(() => {
    setShowAllConditions(!showAll)
  }, [setShowAllConditions, showAll])

  return { showAll, toggleShowAll }
}
