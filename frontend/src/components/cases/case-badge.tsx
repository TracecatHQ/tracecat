import type { LucideIcon } from "lucide-react"
import type React from "react"
import type { PropsWithChildren } from "react"
import type { CasePriority, CaseSeverity, CaseStatus } from "@/client"
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
      className={cn(defaultColor, "items-center gap-1", color, className)}
    >
      <Icon className="stroke-inherit/5 size-3 flex-1" strokeWidth={3} />
      <span className="text-xs">{label}</span>
    </Badge>
  )
}
