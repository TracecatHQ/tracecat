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
    <span className={cn("flex items-center gap-2", className)}>
      {Icon && <Icon className={cn("size-3.5", iconClassName)} />}
      <span className={cn("text-xs font-medium", labelClassName)}>
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
      className={cn("text-xs", className)}
      {...props}
    >
      <SqlTypeDisplay type={type} className="gap-1.5" iconClassName="size-3" />
    </Badge>
  )
}
