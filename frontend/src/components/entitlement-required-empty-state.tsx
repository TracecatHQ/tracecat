import { Lock } from "lucide-react"
import type { ReactNode } from "react"

import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { cn } from "@/lib/utils"

type EntitlementRequiredEmptyStateProps = {
  title: string
  description: ReactNode
  icon?: ReactNode
  className?: string
  children?: ReactNode
}

/**
 * Reusable empty state for routes or sections that remain visible
 * but are unavailable without the required entitlement.
 */
export function EntitlementRequiredEmptyState({
  title,
  description,
  icon,
  className,
  children,
}: EntitlementRequiredEmptyStateProps) {
  const iconNode = icon ?? <Lock className="h-6 w-6" />

  return (
    <Empty className={cn("gap-4 py-12", className)}>
      <EmptyHeader>
        <EmptyMedia variant="icon">{iconNode}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      {children}
    </Empty>
  )
}
