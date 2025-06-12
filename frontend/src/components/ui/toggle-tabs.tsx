"use client"

import * as React from "react"
import { useState } from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export interface ToggleTabOption<T = string> {
  /** Unique identifier for this tab option */
  value: T
  /** React node to render inside the tab (can be text, icons, or any JSX) */
  content: React.ReactNode
  /** Optional tooltip text to show on hover */
  tooltip?: string
  /** Optional aria-label for accessibility */
  ariaLabel?: string
}

const toggleTabVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      size: {
        sm: "h-6 px-2 text-xs",
        md: "h-7 px-3 text-sm",
        lg: "h-8 px-4 text-base",
      },
      active: {
        true: "bg-background text-accent-foreground",
        false:
          "bg-accent text-muted-foreground hover:bg-muted/50 hover:text-accent-foreground",
      },
      position: {
        first: "rounded-l-md border-r-0",
        last: "rounded-r-md",
        single: "rounded-md",
        middle: "border-r-0",
      },
    },
    defaultVariants: {
      size: "md",
      active: false,
      position: "middle",
    },
  }
)

export interface ToggleTabsProps<T = string>
  extends Omit<
      React.HTMLAttributes<HTMLDivElement>,
      "onValueChange" | "defaultValue"
    >,
    VariantProps<typeof toggleTabVariants> {
  /** Array of tab options with custom content */
  options: ToggleTabOption<T>[]
  /** Currently selected tab value */
  value?: T
  /** Default selected tab value (used if value is not provided) */
  defaultValue?: T
  /** Callback fired when tab selection changes */
  onValueChange?: (value: T) => void
  /** Whether to show tooltips */
  showTooltips?: boolean
}

export function ToggleTabs<T = string>({
  options,
  value,
  defaultValue,
  onValueChange,
  className,
  size = "md",
  showTooltips = true,
  ...props
}: ToggleTabsProps<T>) {
  const [internalValue, setInternalValue] = useState<T>(
    value ?? defaultValue ?? options[0]?.value
  )

  const currentValue = value ?? internalValue

  const handleValueChange = (newValue: T) => {
    if (value === undefined) {
      setInternalValue(newValue)
    }
    onValueChange?.(newValue)
  }

  const TabButton = React.memo(
    ({ option, index }: { option: ToggleTabOption<T>; index: number }) => {
      const isActive = currentValue === option.value
      const isFirst = index === 0
      const isLast = index === options.length - 1

      const position =
        options.length === 1
          ? "single"
          : isFirst
            ? "first"
            : isLast
              ? "last"
              : "middle"

      const button = (
        <button
          type="button"
          onClick={() => handleValueChange(option.value)}
          className={cn(
            toggleTabVariants({ size, active: isActive, position })
          )}
          aria-pressed={isActive}
          aria-label={option.ariaLabel}
          {...(isActive && { "aria-current": "true" })}
        >
          {option.content}
        </button>
      )

      if (showTooltips && option.tooltip) {
        return (
          <TooltipProvider key={option.value as string}>
            <Tooltip>
              <TooltipTrigger asChild>{button}</TooltipTrigger>
              <TooltipContent>
                <p>{option.tooltip}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )
      }

      return (
        <React.Fragment key={option.value as string}>{button}</React.Fragment>
      )
    }
  )

  TabButton.displayName = "TabButton"

  if (options.length === 0) {
    return null
  }

  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex items-center rounded-md border border-input bg-transparent",
        className
      )}
      {...props}
    >
      {options.map((option, index) => (
        <TabButton key={option.value as string} option={option} index={index} />
      ))}
    </div>
  )
}
