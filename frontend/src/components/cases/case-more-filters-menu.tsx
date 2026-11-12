"use client"

import { CircleIcon, ListFilterIcon, ListIcon } from "lucide-react"
import type { CaseDropdownDefinitionRead } from "@/client"
import { DynamicLucideIcon } from "@/components/dynamic-lucide-icon"
import type { FilterOption } from "@/components/filters/filter-multi-select"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { DropdownFilterState } from "@/hooks/use-cases"
import { cn } from "@/lib/utils"

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

interface CaseMoreFiltersMenuProps {
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
  dropdownFilters: Record<string, DropdownFilterState>
  onDropdownFilterChange: (ref: string, values: string[]) => void
}

/** Filter icon menu for custom dropdown fields. */
export function CaseMoreFiltersMenu({
  dropdownDefinitions,
  dropdownFilters,
  onDropdownFilterChange,
}: CaseMoreFiltersMenuProps) {
  const definitions = dropdownDefinitions ?? []
  if (definitions.length === 0) {
    return null
  }

  const activeCount = definitions.filter((def) =>
    isDropdownFilterActive(dropdownFilters[def.ref])
  ).length

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="More filters"
          title="More filters"
          className={cn(
            "flex h-6 items-center gap-1 rounded-md border border-input bg-transparent px-1.5 text-xs font-medium transition-colors",
            "hover:bg-muted/50",
            activeCount > 0 && "border-primary/50 bg-primary/5"
          )}
        >
          <ListFilterIcon className="size-3.5 text-muted-foreground" />
          {activeCount > 0 && (
            <span className="text-[10px] font-medium text-muted-foreground">
              {activeCount}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        {definitions.map((def) => (
          <DropdownFilterSubMenu
            key={def.id}
            definition={def}
            state={dropdownFilters[def.ref]}
            onChange={(values) => onDropdownFilterChange(def.ref, values)}
          />
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function DropdownFilterSubMenu({
  definition,
  state,
  onChange,
}: {
  definition: CaseDropdownDefinitionRead
  state: DropdownFilterState | undefined
  onChange: (values: string[]) => void
}) {
  const values = state?.values ?? []
  const valueSet = new Set(values)
  const options = getDropdownFilterOptions(definition)

  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger className="gap-2">
        <DropdownDefinitionIcon definition={definition} />
        <span className="truncate">{definition.name}</span>
        {values.length > 0 && (
          <span className="text-[10px] font-medium text-muted-foreground">
            {values.length}
          </span>
        )}
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className="max-h-80 w-52 overflow-y-auto">
        {options.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted-foreground">No options</p>
        )}
        {options.map((option) => {
          const isSelected = valueSet.has(option.value)
          return (
            <DropdownMenuCheckboxItem
              key={option.value}
              checked={isSelected}
              onSelect={(event) => event.preventDefault()}
              onCheckedChange={() =>
                onChange(
                  isSelected
                    ? values.filter((value) => value !== option.value)
                    : [...values, option.value]
                )
              }
              className="gap-2"
            >
              {option.renderIcon?.()}
              <span className="truncate" style={option.labelStyle}>
                {option.label}
              </span>
            </DropdownMenuCheckboxItem>
          )
        })}
        {values.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="justify-center text-muted-foreground"
              onSelect={() => onChange([])}
            >
              Clear selection
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  )
}
