"use client"

import { Check, type LucideIcon } from "lucide-react"
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

/** One selectable row in a {@link CaseTaskFieldMenu}. */
export interface CaseTaskFieldMenuItem {
  /** Stable value reported through `onSelect`. */
  value: string
  label: string
  icon: LucideIcon
  /** Color utilities for `icon`. */
  iconClassName?: string
}

/** Props for {@link CaseTaskFieldMenu}. */
export interface CaseTaskFieldMenuProps {
  items: readonly CaseTaskFieldMenuItem[]
  /** Currently selected value; the row carrying it gets the check. */
  value: string
  onSelect: (value: string) => void
  ariaLabel: string
  /** Optional hover tooltip on the trigger. */
  tooltip?: ReactNode
  /** The trigger element. */
  children: ReactNode
}

/**
 * Plain dropdown for a task's closed-set fields: status and priority. Four and
 * six fixed options respectively, so there is nothing to search and nothing to
 * multi-select — it mirrors the properties rail's `Select`, an icon-and-label
 * row per option with a check on the current one.
 *
 * Distinct from {@link CaseTaskFieldPicker} on purpose: assignee and workflow
 * are open-ended lists where search earns the extra chrome, these are not.
 * Panel-level `1`–`6` shortcuts stay inert while the menu is open — its content
 * is portaled into a Radix popper wrapper, which `use-digit-shortcuts.ts`
 * treats as a blocking layer.
 */
export function CaseTaskFieldMenu({
  items,
  value,
  onSelect,
  ariaLabel,
  tooltip,
  children,
}: CaseTaskFieldMenuProps) {
  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape") {
      return
    }
    // The menu owns its Escape level. Radix portals the content to
    // document.body, but React synthetic events still bubble through the
    // React tree — without stopPropagation this Escape would also cancel the
    // composer, or close the whole case panel in slideover mode.
    event.stopPropagation()
  }

  const trigger = <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>

  return (
    // Non-modal: the panel behind the menu keeps its scroll and pointer
    // events, exactly as it did under the popover this replaced.
    <DropdownMenu modal={false}>
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
      <DropdownMenuContent
        align="start"
        className="w-40"
        aria-label={ariaLabel}
        onKeyDown={handleKeyDown}
        onClick={(event) => event.stopPropagation()}
      >
        {items.map((item) => {
          const Icon = item.icon
          const selected = item.value === value
          return (
            <DropdownMenuItem
              key={item.value}
              className="gap-2"
              onSelect={() => onSelect(item.value)}
            >
              <Icon className={cn("size-3.5 shrink-0", item.iconClassName)} />
              <span className="truncate">{item.label}</span>
              {selected && (
                <>
                  <Check
                    className="ml-auto size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                  {/* Menu items carry no checked state, so current-ness rides
                      on the accessible name instead. */}
                  <span className="sr-only">{", current"}</span>
                </>
              )}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
