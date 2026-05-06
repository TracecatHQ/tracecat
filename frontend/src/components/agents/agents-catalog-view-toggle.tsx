"use client"

import { MousePointerClickIcon, TagIcon } from "lucide-react"
import Link from "next/link"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export enum AgentsCatalogViewMode {
  Agents = "agents",
  Tags = "tags",
}

interface AgentsCatalogViewToggleProps {
  view: AgentsCatalogViewMode
  className?: string
  agentsHref: string
  tagsHref: string
}

export function AgentsCatalogViewToggle({
  view,
  className,
  agentsHref,
  tagsHref,
}: AgentsCatalogViewToggleProps) {
  const toggleItems = [
    {
      mode: AgentsCatalogViewMode.Agents,
      icon: MousePointerClickIcon,
      tooltip: "Agents",
      href: agentsHref,
      ariaLabel: "Agents view",
    },
    {
      mode: AgentsCatalogViewMode.Tags,
      icon: TagIcon,
      tooltip: "Agent tags",
      href: tagsHref,
      ariaLabel: "Agent tags view",
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

          return (
            <Tooltip key={item.mode}>
              <TooltipTrigger asChild>
                <Link
                  href={item.href}
                  className={baseClasses}
                  aria-current={isActive ? "page" : undefined}
                  aria-label={item.ariaLabel}
                >
                  <Icon className="size-3.5" />
                </Link>
              </TooltipTrigger>
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
