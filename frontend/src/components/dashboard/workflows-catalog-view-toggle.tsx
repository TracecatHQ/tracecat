"use client"

import { TagIcon, WorkflowIcon } from "lucide-react"
import Link from "next/link"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export enum WorkflowsCatalogViewMode {
  Workflows = "workflows",
  Tags = "tags",
}

interface WorkflowsCatalogViewToggleProps {
  view: WorkflowsCatalogViewMode
  onViewChange?: (view: WorkflowsCatalogViewMode) => void
  className?: string
  workflowsHref?: string
  tagsHref?: string
}

export function WorkflowsCatalogViewToggle({
  view,
  onViewChange,
  className,
  workflowsHref,
  tagsHref,
}: WorkflowsCatalogViewToggleProps) {
  const handleViewChange = (nextView: WorkflowsCatalogViewMode) => {
    onViewChange?.(nextView)
  }

  const toggleItems = [
    {
      mode: WorkflowsCatalogViewMode.Workflows,
      icon: WorkflowIcon,
      tooltip: "Workflows",
      href: workflowsHref,
      ariaLabel: "Workflows view",
    },
    {
      mode: WorkflowsCatalogViewMode.Tags,
      icon: TagIcon,
      tooltip: "Workflow tags",
      href: tagsHref,
      ariaLabel: "Workflow tags view",
    },
  ] as const

  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border bg-transparent",
        className
      )}
    >
      <TooltipProvider>
        {toggleItems.map((item, index) => {
          const Icon = item.icon
          const isActive = view === item.mode
          const isFirst = index === 0
          const isLast = index === toggleItems.length - 1
          const roundedClass = cn({
            "rounded-l-sm": isFirst,
            "rounded-none": !isFirst && !isLast,
            "rounded-r-sm": isLast,
          })
          const baseClasses = cn(
            "flex size-7 items-center justify-center transition-colors",
            roundedClass,
            isActive
              ? "bg-background text-accent-foreground"
              : "bg-accent text-muted-foreground hover:bg-muted/50"
          )

          const content = item.href ? (
            <Link
              href={item.href}
              onClick={() => handleViewChange(item.mode)}
              className={baseClasses}
              aria-current={isActive ? "page" : undefined}
              aria-label={item.ariaLabel}
            >
              <Icon className="size-3.5" />
            </Link>
          ) : (
            <button
              type="button"
              onClick={() => handleViewChange(item.mode)}
              className={baseClasses}
              aria-current={isActive}
              aria-label={item.ariaLabel}
            >
              <Icon className="size-3.5" />
            </button>
          )

          return (
            <Tooltip key={item.mode}>
              <TooltipTrigger asChild>{content}</TooltipTrigger>
              <TooltipContent>
                <p>{item.tooltip}</p>
              </TooltipContent>
            </Tooltip>
          )
        })}
      </TooltipProvider>
    </div>
  )
}
