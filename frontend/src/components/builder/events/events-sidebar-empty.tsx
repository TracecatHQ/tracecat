import { type LucideIcon, Plus, WorkflowIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { cn } from "@/lib/utils"

export interface EventsSidebarEmptyProps
  extends React.HTMLAttributes<HTMLDivElement> {
  title?: string
  description?: React.ReactNode | string
  action?: () => void
  icon?: LucideIcon
  actionLabel?: string
}

export function EventsSidebarEmpty({
  title = "No items yet",
  description = "Get started by creating your first item",
  action,
  icon: Icon = WorkflowIcon,
  actionLabel = "Create item",
  className,
  ...props
}: EventsSidebarEmptyProps) {
  return (
    <Empty className={cn("size-full", className)} {...props}>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          {Icon && <Icon className="size-6" />}
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      {action && (
        <EmptyContent>
          <Button
            variant="outline"
            size="sm"
            onClick={action}
            className="w-full gap-1.5"
          >
            <Plus className="size-4" />
            {actionLabel}
          </Button>
        </EmptyContent>
      )}
    </Empty>
  )
}
