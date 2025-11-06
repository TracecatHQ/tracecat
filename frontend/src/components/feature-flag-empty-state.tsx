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

type FeatureFlagEmptyStateProps = {
  title: string
  description: ReactNode
  icon?: ReactNode
  className?: string
  children?: ReactNode
}

export function FeatureFlagEmptyState({
  title,
  description,
  icon,
  className,
  children,
}: FeatureFlagEmptyStateProps) {
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
