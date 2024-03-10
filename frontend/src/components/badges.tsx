import React, { PropsWithChildren } from "react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

interface StatusBadgeProps
  extends PropsWithChildren<React.HTMLAttributes<HTMLElement>> {
  status: string
}

export function StatusBadge({ status, children }: StatusBadgeProps) {
  let bg_color
  let border_color
  let text_color

  switch (status) {
    case "medium":
      bg_color = "bg-blue-100"
      border_color = "border-blue-400"
      text_color = "text-blue-700"
      break
    case "high":
      bg_color = "bg-orange-100"
      border_color = "border-orange-400"
      text_color = "text-orange-700"
      break
    case "critical":
      bg_color = "bg-red-100"
      border_color = "border-red-400"
      text_color = "text-red-700"
      break
    case "malicious":
      bg_color = "bg-red-100"
      border_color = "border-red-400"
      text_color = "text-red-700"
      break
    case "critical":
      bg_color = "bg-red-100"
      border_color = "border-red-400"
      text_color = "text-red-700"
      break
    case "success":
      bg_color = "bg-green-100"
      border_color = "border-green-400"
      text_color = "text-green-700"
      break
    default:
      bg_color = "bg-gray-100"
      border_color = "border-gray-400"
      text_color = "text-gray-700"
  }

  return (
    <Badge variant="outline" className={cn(bg_color, border_color, text_color)}>
      {children}
    </Badge>
  )
}
