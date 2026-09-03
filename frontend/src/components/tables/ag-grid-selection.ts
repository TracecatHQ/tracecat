import type { SelectionEventSourceType } from "ag-grid-community"

/**
 * Selection sources that originate from a direct user gesture. Anything absent
 * from this set (API calls, row data replacement, grid initialization) is
 * treated as programmatic.
 */
const USER_SELECTION_SOURCES: ReadonlySet<SelectionEventSourceType> =
  new Set<SelectionEventSourceType>([
    "checkboxSelected",
    "rowClicked",
    "spaceKey",
    "uiSelectAll",
    "uiSelectAllFiltered",
    "uiSelectAllCurrentPage",
    "keyboardSelectAll",
  ])

/**
 * Whether a selection change came from the user rather than the grid API or a
 * rowData replacement. Allowlist, so unknown sources are treated as
 * programmatic.
 */
export function isUserSelectionSource(
  source: SelectionEventSourceType
): boolean {
  return USER_SELECTION_SOURCES.has(source)
}

/**
 * Merge one page's checkbox state into a selection spanning pages:
 * (previous - visibleIds) union selectedVisibleIds, preserving previous order
 * then appending new ids, no duplicates.
 */
export function reconcileSelection({
  previous,
  visibleIds,
  selectedVisibleIds,
}: {
  previous: ReadonlySet<string>
  visibleIds: readonly string[]
  selectedVisibleIds: readonly string[]
}): string[] {
  const visible = new Set(visibleIds)
  const selectedVisible = new Set(selectedVisibleIds)

  const next: string[] = []
  const seen = new Set<string>()
  for (const id of previous) {
    if (visible.has(id) && !selectedVisible.has(id)) continue
    if (seen.has(id)) continue
    seen.add(id)
    next.push(id)
  }
  for (const id of selectedVisibleIds) {
    if (seen.has(id)) continue
    seen.add(id)
    next.push(id)
  }
  return next
}
