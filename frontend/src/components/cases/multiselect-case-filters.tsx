"use client"

import { Check, ChevronsUpDown } from "lucide-react"
import { type ComponentType, type ReactNode, useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
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

export interface CaseFilterOption<T extends string = string> {
  value: T
  label: string
  icon?: ComponentType<{ className?: string }>
  iconClassName?: string
  description?: ReactNode
}

export interface CaseFilterMultiSelectProps<T extends string> {
  placeholder: string
  value: T[]
  options: CaseFilterOption<T>[]
  onChange: (value: T[]) => void
  className?: string
  emptyMessage?: string
}

export function CaseFilterMultiSelect<T extends string>({
  placeholder,
  value,
  options,
  onChange,
  className,
  emptyMessage = "No results found.",
}: CaseFilterMultiSelectProps<T>) {
  const [open, setOpen] = useState(false)

  const valueSet = useMemo(() => new Set(value), [value])
  const optionMap = useMemo(() => {
    const map = new Map<T, CaseFilterOption<T>>()
    for (const option of options) {
      map.set(option.value, option)
    }
    return map
  }, [options])

  const selectedCount = value.length
  const triggerLabel =
    selectedCount === 0
      ? placeholder
      : selectedCount === 1
        ? (optionMap.get(value[0])?.label ?? placeholder)
        : `${placeholder} (${selectedCount})`

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          role="combobox"
          className={cn("h-8 w-full justify-between px-3 text-xs", className)}
        >
          <span className="truncate text-left">{triggerLabel}</span>
          <ChevronsUpDown className="ml-2 size-3 opacity-50" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[220px] p-0 sm:w-[260px]"
        align="start"
        side="top"
        sideOffset={6}
        avoidCollisions={false}
        style={{ width: "var(--radix-popover-trigger-width)" }}
      >
        <Command>
          <CommandInput
            placeholder={`Search ${placeholder.toLowerCase()}...`}
            className="text-xs"
          />
          <CommandList>
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const isSelected = valueSet.has(option.value)
                const Icon = option.icon
                return (
                  <CommandItem
                    key={option.value}
                    value={`${option.label} ${option.value}`}
                    className="flex items-center gap-2 text-xs"
                    onSelect={() => {
                      const nextValue = isSelected
                        ? value.filter((item) => item !== option.value)
                        : [...value, option.value]
                      onChange(nextValue)
                      setOpen(true)
                    }}
                  >
                    <div
                      className={cn(
                        "mr-2 flex size-4 items-center justify-center rounded-sm border",
                        isSelected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-muted text-muted-foreground"
                      )}
                    >
                      <Check
                        className={cn("size-3", !isSelected && "opacity-0")}
                        aria-hidden
                      />
                    </div>
                    {Icon ? (
                      <Icon
                        className={cn(
                          "size-3.5 text-muted-foreground",
                          option.iconClassName
                        )}
                        aria-hidden
                      />
                    ) : null}
                    <span className="truncate">{option.label}</span>
                    {option.description ? (
                      <span className="ml-auto text-[11px] text-muted-foreground">
                        {option.description}
                      </span>
                    ) : null}
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
        {selectedCount > 0 && (
          <div className="border-t p-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-full text-xs"
              onClick={() => onChange([])}
            >
              Clear selection
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
