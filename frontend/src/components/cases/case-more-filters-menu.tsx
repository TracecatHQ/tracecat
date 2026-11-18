"use client"

import { CircleIcon, ListIcon } from "lucide-react"
import type { CaseDropdownDefinitionRead } from "@/client"
import { DynamicLucideIcon } from "@/components/dynamic-lucide-icon"
import type { FilterOption } from "@/components/filters/filter-multi-select"
import type { DropdownFilterState } from "@/lib/case-query"

/** Build filter options for a custom dropdown field definition. */
export function getDropdownFilterOptions(
  definition: CaseDropdownDefinitionRead
): FilterOption<string>[] {
  return (
    definition.options?.map((opt) => {
      const optionColorStyle = opt.color
        ? ({ color: opt.color } as React.CSSProperties)
        : undefined
      const fallbackIcon = opt.color ? (
        <CircleIcon className="size-3.5 shrink-0" style={optionColorStyle} />
      ) : (
        <CircleIcon className="size-3.5 shrink-0 text-muted-foreground" />
      )

      return {
        value: opt.ref,
        label: opt.label,
        renderIcon: () =>
          opt.icon_name ? (
            <DynamicLucideIcon
              name={opt.icon_name}
              className="size-3.5 shrink-0"
              style={optionColorStyle}
              fallback={fallbackIcon}
            />
          ) : (
            fallbackIcon
          ),
        labelStyle: optionColorStyle,
      }
    }) ?? []
  )
}

/** Render the icon configured on a custom dropdown field definition. */
export function DropdownDefinitionIcon({
  definition,
}: {
  definition: CaseDropdownDefinitionRead
}) {
  const fallback = <ListIcon className="size-3.5 text-muted-foreground" />
  if (!definition.icon_name) {
    return fallback
  }
  return (
    <DynamicLucideIcon
      name={definition.icon_name}
      className="size-3.5 text-muted-foreground"
      fallback={fallback}
    />
  )
}

/** Whether a custom dropdown filter narrows the current view. */
export function isDropdownFilterActive(
  state: DropdownFilterState | undefined
): boolean {
  return Boolean(state && state.values.length > 0)
}
