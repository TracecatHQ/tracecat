"use client"

import type { LucideIcon } from "lucide-react"
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useState,
} from "react"
import { CheckIndicator } from "@/components/ui/check-indicator"
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

/** One selectable row in a {@link CaseTaskFieldPicker} palette. */
export interface CaseTaskFieldPickerItem {
  /** Stable value reported through `onSelect`. */
  value: string
  label: string
  icon?: LucideIcon
  /** Color utilities for `icon`. */
  iconClassName?: string
  /** Custom leading node (an avatar, say) rendered in place of `icon`. */
  leading?: ReactNode
  /** Extra text the palette search matches beyond the label. */
  searchValue?: string
}

/** Props for {@link CaseTaskFieldPicker}. */
export interface CaseTaskFieldPickerProps {
  items: readonly CaseTaskFieldPickerItem[]
  /** Currently selected value; `null` selects the `emptyValue` item, if any. */
  value: string | null
  onSelect: (value: string | null) => void
  /** Search input placeholder: "Change status…", "Assign to…", … */
  placeholder: string
  ariaLabel: string
  /**
   * Item value reported as `null` — the "No assignee" / "No workflow" row.
   * Sentinels never leak: callers keep them file-local.
   */
  emptyValue?: string
  /** Optional hover tooltip on the trigger. */
  tooltip?: ReactNode
  /** The trigger element. */
  children: ReactNode
}

/**
 * The searchable palette behind the task fields whose option list is open
 * ended: the assignee, and the composer's workflow pill. A `Popover` +
 * `Command` in the `case-tag-picker.tsx` mould: search input and
 * `CheckIndicator` on the current value. Status and priority deliberately do
 * not come through here — they are closed sets of four and six, so they use
 * the plain {@link CaseTaskFieldMenu} dropdown, with no search box and no
 * checkbox. Digits carry no shortcuts here — they only ever type into the
 * search. Panel-level `1`–`6`
 * shortcuts stay inert while the palette is open: its content is portaled
 * into a Radix popper wrapper, which `use-digit-shortcuts.ts` treats as a
 * blocking layer.
 */
export function CaseTaskFieldPicker({
  items,
  value,
  onSelect,
  placeholder,
  ariaLabel,
  emptyValue,
  tooltip,
  children,
}: CaseTaskFieldPickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) {
      setQuery("")
    }
  }

  function selectItem(item: CaseTaskFieldPickerItem) {
    handleOpenChange(false)
    onSelect(item.value === emptyValue ? null : item.value)
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape") {
      return
    }
    // The palette owns its Escape level. Radix portals the content to
    // document.body, but React synthetic events still bubble through the
    // React tree — without stopPropagation this Escape would also cancel
    // the composer, or close the whole case panel in slideover mode.
    event.preventDefault()
    event.stopPropagation()
    handleOpenChange(false)
  }

  const trigger = <PopoverTrigger asChild>{children}</PopoverTrigger>

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      {tooltip ? (
        <Tooltip>
          <TooltipTrigger asChild>{trigger}</TooltipTrigger>
          <TooltipContent side="top" className="text-xs">
            {tooltip}
          </TooltipContent>
        </Tooltip>
      ) : (
        trigger
      )}
      <PopoverContent
        align="start"
        className="w-56 p-0"
        onClick={(e) => e.stopPropagation()}
      >
        <Command aria-label={ariaLabel} onKeyDown={handleKeyDown}>
          <CommandInput
            placeholder={placeholder}
            className="text-xs"
            value={query}
            onValueChange={setQuery}
          />
          <CommandList>
            <CommandEmpty>No results found.</CommandEmpty>
            <CommandGroup>
              {items.map((item) => {
                const Icon = item.icon
                const selected = (value ?? emptyValue) === item.value
                return (
                  <CommandItem
                    key={item.value}
                    // The label and searchValue are what the user searches;
                    // the raw value tags along so identical labels stay
                    // unique for cmdk.
                    value={[item.label, item.searchValue, item.value]
                      .filter(Boolean)
                      .join(" ")}
                    className="group text-xs"
                    onSelect={() => selectItem(item)}
                  >
                    <CheckIndicator checked={selected} />
                    {item.leading ??
                      (Icon ? <Icon className={item.iconClassName} /> : null)}
                    <span className="truncate">{item.label}</span>
                    {/* cmdk owns `aria-selected` for highlight, so current
                        state rides on the accessible name instead. */}
                    {selected && <span className="sr-only">{", current"}</span>}
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
