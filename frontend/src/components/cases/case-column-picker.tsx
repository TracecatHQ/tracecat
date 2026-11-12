"use client"

import {
  ChevronDown,
  ListIcon,
  ListTreeIcon,
  SlidersHorizontalIcon,
  TextIcon,
  TimerIcon,
} from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import type {
  CaseDropdownDefinitionRead,
  CaseDurationDefinitionRead,
  CaseFieldReadMinimal,
} from "@/client"
import { DynamicLucideIcon } from "@/components/dynamic-lucide-icon"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

const MAX_VISIBLE_COLUMNS = 4

const COLUMN_ICON_CLASS = "size-3.5 shrink-0 text-muted-foreground"

interface ColumnOption {
  columnId: string
  label: string
  icon: ReactNode
}

function ColumnPill({
  option,
  isSelected,
  isDisabled,
  onToggle,
}: {
  option: ColumnOption
  isSelected: boolean
  isDisabled: boolean
  onToggle: (columnId: string) => void
}) {
  return (
    <button
      type="button"
      aria-pressed={isSelected}
      disabled={isDisabled}
      title={isDisabled ? `Up to ${MAX_VISIBLE_COLUMNS} columns` : undefined}
      onClick={() => onToggle(option.columnId)}
      className={cn(
        "flex h-6 max-w-full items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors hover:bg-muted/50",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent",
        isSelected && "border-primary/50 bg-primary/5"
      )}
    >
      {option.icon}
      <span className="truncate">{option.label}</span>
    </button>
  )
}

interface CaseColumnPickerProps {
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
  fieldDefinitions?: CaseFieldReadMinimal[]
  durationDefinitions?: CaseDurationDefinitionRead[]
  visibleColumnIds?: string[]
  onToggle?: (columnId: string) => void
  includesSubCases?: boolean
  hierarchyDisabled?: boolean
  onIncludeSubCasesChange?: (value: boolean) => void
}

export function CaseColumnPicker({
  dropdownDefinitions,
  fieldDefinitions,
  durationDefinitions,
  visibleColumnIds = [],
  onToggle = () => {},
  includesSubCases = false,
  hierarchyDisabled = false,
  onIncludeSubCasesChange,
}: CaseColumnPickerProps) {
  const [open, setOpen] = useState(false)

  const columnOptions = useMemo<ColumnOption[]>(() => {
    const options: ColumnOption[] = []
    for (const def of dropdownDefinitions ?? []) {
      options.push({
        columnId: `dropdown:${def.ref}`,
        label: def.name,
        icon: def.icon_name ? (
          <DynamicLucideIcon
            name={def.icon_name}
            className={COLUMN_ICON_CLASS}
            fallback={<ListIcon className={COLUMN_ICON_CLASS} />}
          />
        ) : (
          <ListIcon className={COLUMN_ICON_CLASS} />
        ),
      })
    }
    for (const field of fieldDefinitions ?? []) {
      if (field.reserved) continue
      options.push({
        columnId: `field:${field.id}`,
        label: field.display_name,
        icon: <TextIcon className={COLUMN_ICON_CLASS} />,
      })
    }
    for (const def of durationDefinitions ?? []) {
      options.push({
        columnId: `duration:${def.id}`,
        label: def.name,
        icon: <TimerIcon className={COLUMN_ICON_CLASS} />,
      })
    }
    return options
  }, [dropdownDefinitions, fieldDefinitions, durationDefinitions])

  // Only count known column IDs so stale persisted IDs don't count
  // toward the max-4 limit or show in the badge count.
  const selectedCount = columnOptions.filter((option) =>
    visibleColumnIds.includes(option.columnId)
  ).length
  const isAtLimit = selectedCount >= MAX_VISIBLE_COLUMNS

  if (columnOptions.length === 0 && !onIncludeSubCasesChange) {
    return null
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors",
            "hover:bg-muted/50",
            selectedCount > 0 && "border-primary/50 bg-primary/5"
          )}
        >
          <SlidersHorizontalIcon className="size-3.5 text-muted-foreground" />
          <span>Display</span>
          {selectedCount > 0 && (
            <span className="ml-0.5 text-[10px] font-medium text-muted-foreground">
              {selectedCount}
            </span>
          )}
          <ChevronDown className="size-3 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-72 divide-y p-0 shadow-sm"
        align="end"
        sideOffset={4}
      >
        {onIncludeSubCasesChange && (
          <label
            className={cn(
              "flex items-center justify-between gap-2 px-3 py-2.5 text-xs font-medium",
              hierarchyDisabled
                ? "cursor-not-allowed opacity-50"
                : "cursor-pointer"
            )}
          >
            <span className="flex items-center gap-2">
              <ListTreeIcon className={COLUMN_ICON_CLASS} />
              Parents only
            </span>
            <Switch
              checked={!includesSubCases}
              disabled={hierarchyDisabled}
              onCheckedChange={(checked) => onIncludeSubCasesChange(!checked)}
            />
          </label>
        )}
        {columnOptions.length > 0 && (
          <div className="space-y-2 px-3 py-2.5">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Columns</span>
              <span className="tabular-nums">
                {selectedCount}/{MAX_VISIBLE_COLUMNS}
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {columnOptions.map((option) => {
                const isSelected = visibleColumnIds.includes(option.columnId)
                return (
                  <ColumnPill
                    key={option.columnId}
                    option={option}
                    isSelected={isSelected}
                    isDisabled={!isSelected && isAtLimit}
                    onToggle={onToggle}
                  />
                )
              })}
            </div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
