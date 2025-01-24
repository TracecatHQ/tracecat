import { LucideIcon, Plus, WorkflowIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

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
  if (typeof description === "string") {
    description = <p className="text-xs text-muted-foreground">{description}</p>
  }
  return (
    <div
      id="outer"
      className={cn(
        "flex size-full flex-col items-center justify-center",
        className
      )}
      {...props}
    >
      <div className="flex flex-col items-center gap-4 p-6 text-center">
        <div className="rounded-full bg-muted p-3">
          {Icon && <Icon className="size-6 text-muted-foreground" />}
        </div>
        <div className="space-y-1">
          <h4 className="text-sm font-semibold">{title}</h4>
          {description}
        </div>
        {action && (
          <Button
            variant="outline"
            size="sm"
            onClick={action}
            className="w-full gap-1.5"
          >
            <Plus className="size-4" />
            {actionLabel}
          </Button>
        )}
      </div>
    </div>
  )
}
