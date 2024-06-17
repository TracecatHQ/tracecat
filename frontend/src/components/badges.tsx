import React, { PropsWithChildren } from "react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

interface StatusBadgeProps
  extends PropsWithChildren<React.HTMLAttributes<HTMLElement>> {
  status?: string
}

const statusColors = {
  normal: "bg-blue-100/70 border-blue-600/70 text-blue-700/80",
  low: "bg-yellow-100/80 border-yellow-600/70 text-yellow-700/80",
  medium: "bg-orange-100 border-orange-600 text-orange-700",
  high: "bg-red-100 border-red-400 text-red-700",
  critical: "bg-fuchsia-100 border-fuchsia-400 text-fuchsia-700",
  malicious: "bg-red-100 border-red-400 text-red-700",
  success: "bg-green-100 border-green-600 text-green-700",
  benign: "bg-green-100 border-green-600 text-green-700",
}

const defaultStatusColor = "border-slate-400/70 bg-slate-50 text-slate-600/80"

export function StatusBadge({ status, children, className }: StatusBadgeProps) {
  return (
    <Badge
      variant="outline"
      className={cn(
        defaultStatusColor,
        "items-center gap-1",
        status &&
          status in statusColors &&
          statusColors[status as keyof typeof statusColors],
        className
      )}
    >
      {children}
    </Badge>
  )
}

export function AvailabilityBadge({
  availability,
  className,
}: {
  availability: string
  className?: string
}) {
  switch (availability) {
    case "comingSoon":
      return <ComingSoonBadge className={className} />
    default:
      return null
  }
}

export function ComingSoonBadge({ className }: { className?: string }) {
  return (
    <Badge variant="outline" className={cn("bg-white py-3 text-xs", className)}>
      Coming Soon
    </Badge>
  )
}
