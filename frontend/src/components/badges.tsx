import React, { PropsWithChildren } from "react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

interface StatusBadgeProps
  extends PropsWithChildren<React.HTMLAttributes<HTMLElement>> {
  status?: string
}

const statusColors = {
  low: "bg-cyan-100 border-cyan-600 text-cyan-700",
  medium: "bg-yellow-100 border-yellow-600 text-yellow-700",
  high: "bg-orange-100 border-orange-600 text-orange-700",
  critical: "bg-red-100 border-red-400 text-red-700",
  malicious: "bg-red-100 border-red-400 text-red-700",
  success: "bg-green-100 border-green-600 text-green-700",
  benign: "bg-green-100 border-green-600 text-green-700",
}

export function StatusBadge({ status, children }: StatusBadgeProps) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "border-gray-400 bg-gray-100 text-gray-700",
        status &&
          status in statusColors &&
          statusColors[status as keyof typeof statusColors]
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
    <Badge
      variant="outline"
      className={cn(
        "border-2 border-emerald-500 bg-emerald-200  text-xs text-emerald-700",
        className
      )}
    >
      Coming Soon
    </Badge>
  )
}
