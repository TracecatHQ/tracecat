"use client"

import { Badge, type BadgeProps } from "@/components/ui/badge"
import { getSqlTypeConfig, type SqlType } from "@/lib/data-type"
import { cn } from "@/lib/utils"

interface SqlTypeDisplayProps {
  type: SqlType
  className?: string
  iconClassName?: string
  labelClassName?: string
}

export function SqlTypeDisplay({
  type,
  className,
  iconClassName,
  labelClassName,
}: SqlTypeDisplayProps) {
  const config = getSqlTypeConfig(type)
  const Icon = config?.icon

  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 whitespace-nowrap",
        className
      )}
    >
      {Icon && <Icon className={cn("size-4 shrink-0", iconClassName)} />}
      <span
        className={cn(
          "font-normal leading-none whitespace-nowrap",
          labelClassName
        )}
      >
        {config?.label ?? type}
      </span>
    </span>
  )
}

interface SqlTypeBadgeProps extends Omit<BadgeProps, "children"> {
  type: SqlType
}

export function SqlTypeBadge({ type, className, ...props }: SqlTypeBadgeProps) {
  return (
    <Badge
      variant="secondary"
      className={cn("text-xs whitespace-nowrap", className)}
      {...props}
    >
      <SqlTypeDisplay
        type={type}
        className="gap-1.5"
        iconClassName="size-3 shrink-0"
        labelClassName="text-xs font-medium whitespace-nowrap"
      />
    </Badge>
  )
}
