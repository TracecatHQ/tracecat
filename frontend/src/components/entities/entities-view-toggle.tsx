"use client"

import { BracesIcon, Link as LinkIcon } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export enum EntitiesViewMode {
  Fields = "fields",
  Relations = "relations",
}

interface EntitiesViewToggleProps {
  view: EntitiesViewMode
  onViewChange?: (view: EntitiesViewMode) => void
  className?: string
}

export function EntitiesViewToggle({
  view,
  onViewChange,
  className,
}: EntitiesViewToggleProps) {
  const handleViewChange = (next: EntitiesViewMode) => onViewChange?.(next)

  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border bg-transparent",
        className
      )}
    >
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => handleViewChange(EntitiesViewMode.Fields)}
              className={cn(
                "flex size-7 items-center justify-center rounded-l-sm transition-colors",
                view === EntitiesViewMode.Fields
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={view === EntitiesViewMode.Fields}
              aria-label="Fields view"
            >
              <BracesIcon className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Fields</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => handleViewChange(EntitiesViewMode.Relations)}
              className={cn(
                "flex size-7 items-center justify-center rounded-r-sm transition-colors",
                view === EntitiesViewMode.Relations
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={view === EntitiesViewMode.Relations}
              aria-label="Relations view"
            >
              <LinkIcon className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Relations</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}
