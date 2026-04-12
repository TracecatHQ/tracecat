"use client"

import {
  Check,
  ChevronDown,
  ListIcon,
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
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

const MAX_VISIBLE_COLUMNS = 4

function ColumnCheckbox({
  checked,
  disabled,
}: {
  checked: boolean
  disabled: boolean
}) {
  return (
    <div
      className={cn(
        "mr-2 flex size-4 shrink-0 items-center justify-center rounded-sm border",
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-muted-foreground/40 bg-transparent",
        disabled && "opacity-40"
      )}
    >
      {checked && <Check className="size-3" />}
    </div>
  )
}

function ColumnOption({
  columnId,
  searchValue,
  label,
  icon,
  isSelected,
  isDisabled,
  onToggle,
}: {
  columnId: string
  searchValue: string
  label: string
  icon: ReactNode
  isSelected: boolean
  isDisabled: boolean
  onToggle: (columnId: string) => void
}) {
  return (
    <CommandItem
      key={columnId}
      value={searchValue}
      onSelect={() => !isDisabled && onToggle(columnId)}
      disabled={isDisabled}
      className="text-xs"
    >
      <ColumnCheckbox checked={isSelected} disabled={isDisabled} />
      {icon}
      <span className="truncate">{label}</span>
    </CommandItem>
  )
}

interface CaseColumnPickerProps {
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
  fieldDefinitions?: CaseFieldReadMinimal[]
  durationDefinitions?: CaseDurationDefinitionRead[]
  visibleColumnIds: string[]
  onToggle: (columnId: string) => void
}

export function CaseColumnPicker({
  dropdownDefinitions,
  fieldDefinitions,
  durationDefinitions,
  visibleColumnIds,
  onToggle,
}: CaseColumnPickerProps) {
  const [open, setOpen] = useState(false)
  const selectedCount = visibleColumnIds.length
  const isAtLimit = selectedCount >= MAX_VISIBLE_COLUMNS

  const nonReservedFields = useMemo(
    () => fieldDefinitions?.filter((f) => !f.reserved) ?? [],
    [fieldDefinitions]
  )

  const hasOptions =
    (dropdownDefinitions && dropdownDefinitions.length > 0) ||
    nonReservedFields.length > 0 ||
    (durationDefinitions && durationDefinitions.length > 0)

  if (!hasOptions) {
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
          <span>Columns</span>
          {selectedCount > 0 && (
            <span className="ml-0.5 text-[10px] font-medium text-muted-foreground">
              {selectedCount}
            </span>
          )}
          <ChevronDown className="size-3 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-56 p-0 shadow-sm"
        align="start"
        sideOffset={4}
      >
        <Command>
          <CommandInput
            placeholder="Search columns..."
            className="h-8 text-xs"
          />
          <CommandList>
            <CommandEmpty className="py-3 text-center text-xs text-muted-foreground">
              No columns found.
            </CommandEmpty>

            {dropdownDefinitions && dropdownDefinitions.length > 0 && (
              <CommandGroup heading="Dropdowns">
                {dropdownDefinitions.map((def) => {
                  const columnId = `dropdown:${def.ref}`
                  const isSelected = visibleColumnIds.includes(columnId)
                  return (
                    <ColumnOption
                      key={columnId}
                      columnId={columnId}
                      searchValue={`dropdown ${def.name}`}
                      label={def.name}
                      icon={
                        def.icon_name ? (
                          <DynamicLucideIcon
                            name={def.icon_name}
                            className="mr-2 size-3.5 shrink-0 text-muted-foreground"
                            fallback={
                              <ListIcon className="mr-2 size-3.5 shrink-0 text-muted-foreground" />
                            }
                          />
                        ) : (
                          <ListIcon className="mr-2 size-3.5 shrink-0 text-muted-foreground" />
                        )
                      }
                      isSelected={isSelected}
                      isDisabled={!isSelected && isAtLimit}
                      onToggle={onToggle}
                    />
                  )
                })}
              </CommandGroup>
            )}

            {nonReservedFields.length > 0 && (
              <CommandGroup heading="Fields">
                {nonReservedFields.map((field) => {
                  const columnId = `field:${field.id}`
                  const isSelected = visibleColumnIds.includes(columnId)
                  return (
                    <ColumnOption
                      key={columnId}
                      columnId={columnId}
                      searchValue={`field ${field.id}`}
                      label={field.id}
                      icon={
                        <TextIcon className="mr-2 size-3.5 shrink-0 text-muted-foreground" />
                      }
                      isSelected={isSelected}
                      isDisabled={!isSelected && isAtLimit}
                      onToggle={onToggle}
                    />
                  )
                })}
              </CommandGroup>
            )}

            {durationDefinitions && durationDefinitions.length > 0 && (
              <CommandGroup heading="Durations">
                {durationDefinitions.map((def) => {
                  const columnId = `duration:${def.id}`
                  const isSelected = visibleColumnIds.includes(columnId)
                  return (
                    <ColumnOption
                      key={columnId}
                      columnId={columnId}
                      searchValue={`duration ${def.name}`}
                      label={def.name}
                      icon={
                        <TimerIcon className="mr-2 size-3.5 shrink-0 text-muted-foreground" />
                      }
                      isSelected={isSelected}
                      isDisabled={!isSelected && isAtLimit}
                      onToggle={onToggle}
                    />
                  )
                })}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
