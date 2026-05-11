import { CircleIcon, type LucideIcon } from "lucide-react"
import type { PropsWithChildren } from "react"
import * as React from "react"
import type { CasePriority, CaseSeverity, CaseStatus } from "@/client"
import { DynamicLucideIcon } from "@/components/dynamic-lucide-icon"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type CaseBadgeValue = CaseStatus | CasePriority | CaseSeverity
export interface CaseBadgeProps<T extends CaseBadgeValue>
  extends PropsWithChildren<React.HTMLAttributes<HTMLElement>> {
  value: T
  label: string
  icon: LucideIcon
  color?: string
}

const defaultColor = "border-slate-400/70 bg-slate-50 text-slate-600/80"

export function CaseBadge<T extends CaseBadgeValue>({
  label,
  icon: Icon,
  className,
  color,
}: CaseBadgeProps<T>) {
  return (
    <Badge
      variant="outline"
      className={cn(
        defaultColor,
        "items-center gap-1 border-0 leading-tight",
        color,
        className
      )}
    >
      <Icon
        className="stroke-inherit/5 size-[0.9em] flex-none"
        strokeWidth={3}
      />
      <span>{label}</span>
    </Badge>
  )
}

export interface CaseColumnBadgeProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "content" | "color"> {
  label?: string
  content?: React.ReactNode
  iconName?: string | null
  color?: string | null
}

/** Generic badge for custom columns (dropdowns, fields, durations). */
export const CaseColumnBadge = React.forwardRef<
  HTMLDivElement,
  CaseColumnBadgeProps
>(({ label, content, iconName, color, className, style, ...props }, ref) => {
  const fallbackIcon = (
    <CircleIcon className="size-[0.9em] flex-none" strokeWidth={3} />
  )
  const colorStyle = color
    ? ({ backgroundColor: `${color}20`, color } as React.CSSProperties)
    : undefined
  const mergedStyle = { ...style, ...colorStyle }

  return (
    <Badge
      ref={ref}
      variant="outline"
      // Keep passing DOM props through to the underlying Badge so wrappers like
      // Radix TooltipTrigger(asChild) can attach their hover/focus handlers.
      className={cn(
        !color && "bg-secondary text-secondary-foreground",
        "min-w-0 max-w-[120px] items-center gap-1 border-0 leading-tight transition-opacity hover:opacity-80",
        className
      )}
      style={mergedStyle}
      {...props}
    >
      {iconName ? (
        <DynamicLucideIcon
          name={iconName}
          className="size-[0.9em] flex-none"
          strokeWidth={3}
          fallback={fallbackIcon}
        />
      ) : (
        fallbackIcon
      )}
      {content ?? <span className="min-w-0 flex-1 truncate">{label}</span>}
    </Badge>
  )
})
CaseColumnBadge.displayName = "CaseColumnBadge"
