import { Check } from "lucide-react"
import { cn } from "@/lib/utils"

/**
 * Props for {@link CheckIndicator}.
 */
export interface CheckIndicatorProps {
  /** Whether the option the indicator belongs to is selected. */
  checked: boolean
  /** Renders the indicator dimmed and never reveals it on hover. */
  disabled?: boolean
  className?: string
}

/**
 * Presentational square checkbox indicator for multi-select option rows.
 *
 * The indicator is purely visual: selection is owned by the row it sits in.
 * When unchecked it stays invisible but keeps its box, so labels never shift
 * horizontally. It reveals itself while the row is highlighted, which covers
 * both cmdk rows (`data-selected="true"`) and Radix menu items
 * (`data-highlighted`).
 *
 * The row must carry the `group` class for the hover reveal to work.
 */
export function CheckIndicator({
  checked,
  disabled,
  className,
}: CheckIndicatorProps) {
  return (
    <div
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded-sm border transition-opacity",
        checked
          ? "border-primary bg-primary text-primary-foreground opacity-100"
          : "border-muted-foreground/40 bg-transparent opacity-0 [&_svg]:invisible group-data-[selected=true]:opacity-100 group-data-[highlighted]:opacity-100",
        disabled && "opacity-40",
        className
      )}
    >
      {/* `!size-3` beats the `[&_svg]:size-4` rule cmdk rows put on their children. */}
      <Check className="!size-3" aria-hidden />
    </div>
  )
}
