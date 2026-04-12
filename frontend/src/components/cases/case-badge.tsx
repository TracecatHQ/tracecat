import { CircleIcon, type LucideIcon } from "lucide-react"
import type React from "react"
import type { PropsWithChildren } from "react"
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

export interface CaseColumnBadgeProps {
  label: string
  iconName?: string | null
  color?: string | null
  className?: string
}

/** Generic badge for custom columns (dropdowns, fields, durations). */
export function CaseColumnBadge({
  label,
  iconName,
  color,
  className,
}: CaseColumnBadgeProps) {
  const fallbackIcon = (
    <CircleIcon className="size-[0.9em] flex-none" strokeWidth={3} />
  )
  const colorStyle = color
    ? ({ backgroundColor: `${color}20`, color } as React.CSSProperties)
    : undefined

  return (
    <Badge
      variant="outline"
      className={cn(
        !color && "bg-muted/50 text-muted-foreground",
        "max-w-[120px] items-center gap-1 border-0 leading-tight",
        className
      )}
      style={colorStyle}
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
      <span className="truncate">{label}</span>
    </Badge>
  )
}
