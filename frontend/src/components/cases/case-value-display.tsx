import type { LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

interface CaseValueDisplayProps {
  icon?: LucideIcon
  label: string
  color?: string
  className?: string
}

export function CaseValueDisplay({
  icon: Icon,
  label,
  color,
  className,
}: CaseValueDisplayProps) {
  return (
    <div className={cn("flex items-center gap-1", className)}>
      {Icon && <Icon className={cn("h-3 w-3", color)} strokeWidth={2.5} />}
      <span className={cn("text-xs font-medium", color)}>{label}</span>
    </div>
  )
}
