"use client"

import { BracesIcon, SquareStackIcon, TagIcon, Timer } from "lucide-react"
import Link from "next/link"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export enum CasesViewMode {
  Cases = "cases",
  Tags = "tags",
  CustomFields = "custom-fields",
  Durations = "durations",
}

interface CasesViewToggleProps {
  view: CasesViewMode
  onViewChange?: (view: CasesViewMode) => void
  className?: string
  casesHref?: string
  tagsHref?: string
  customFieldsHref?: string
  durationsHref?: string
}

export function CasesViewToggle({
  view,
  onViewChange,
  className,
  casesHref,
  tagsHref,
  customFieldsHref,
  durationsHref,
}: CasesViewToggleProps) {
  const handleViewChange = (view: CasesViewMode) => {
    onViewChange?.(view)
  }

  const toggleItems = [
    {
      mode: CasesViewMode.Cases,
      icon: SquareStackIcon,
      tooltip: "Cases table",
      href: casesHref,
      ariaLabel: "Cases view",
    },
    {
      mode: CasesViewMode.Tags,
      icon: TagIcon,
      tooltip: "Tags",
      href: tagsHref,
      ariaLabel: "Tags view",
    },
    {
      mode: CasesViewMode.CustomFields,
      icon: BracesIcon,
      tooltip: "Custom fields",
      href: customFieldsHref,
      ariaLabel: "Custom fields view",
    },
    {
      mode: CasesViewMode.Durations,
      icon: Timer,
      tooltip: "Durations",
      href: durationsHref,
      ariaLabel: "Durations view",
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
