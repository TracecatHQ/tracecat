"use client"

import { cva, type VariantProps } from "class-variance-authority"
import * as React from "react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

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

interface TabButtonProps<T> {
  option: ToggleTabOption<T>
  index: number
  optionsCount: number
  currentValue: T
  onValueChange: (value: T) => void
  showTooltips: boolean
  size: VariantProps<typeof toggleTabVariants>["size"]
}

function TabButton<T>({
  option,
  index,
  optionsCount,
  currentValue,
  onValueChange,
  size,
  showTooltips,
}: TabButtonProps<T>) {
  const isActive = currentValue === option.value
  const isFirst = index === 0
  const isLast = index === optionsCount - 1

  const position =
    optionsCount === 1
      ? "single"
      : isFirst
        ? "first"
        : isLast
          ? "last"
          : "middle"

  const button = (
    <button
      type="button"
      onClick={() => onValueChange(option.value)}
      className={cn(toggleTabVariants({ size, active: isActive, position }))}
      aria-pressed={isActive}
      aria-label={option.ariaLabel}
      {...(isActive && { "aria-current": "true" })}
    >
      {option.content}
    </button>
  )

  if (showTooltips && option.tooltip) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>{button}</TooltipTrigger>
          <TooltipContent>
            <p>{option.tooltip}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  return button
}
TabButton.displayName = "TabButton"
const MemoizedTabButton = React.memo(TabButton) as typeof TabButton

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
  const [internalValue, setInternalValue] = React.useState<T>(
    value ?? defaultValue ?? options[0]?.value
  )

  const currentValue = value ?? internalValue

  const handleValueChange = React.useCallback(
    (newValue: T) => {
      if (value === undefined) {
        setInternalValue(newValue)
      }
      onValueChange?.(newValue)
    },
    [value, onValueChange]
  )

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
        <MemoizedTabButton
          key={String(option.value)}
          option={option}
          index={index}
          optionsCount={options.length}
          currentValue={currentValue}
          onValueChange={handleValueChange}
          size={size}
          showTooltips={showTooltips}
        />
      ))}
    </div>
  )
}
