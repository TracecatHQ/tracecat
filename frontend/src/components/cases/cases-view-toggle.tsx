"use client"

import { BracesIcon, SquareStackIcon } from "lucide-react"
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
  CustomFields = "custom-fields",
}

interface CasesViewToggleProps {
  view: CasesViewMode
  onViewChange?: (view: CasesViewMode) => void
  className?: string
  casesHref?: string
  customFieldsHref?: string
}

export function CasesViewToggle({
  view,
  onViewChange,
  className,
  casesHref,
  customFieldsHref,
}: CasesViewToggleProps) {
  const handleViewChange = (view: CasesViewMode) => {
    onViewChange?.(view)
  }

  // Minimal toggle similar to workflows
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
            {casesHref ? (
              <Link
                href={casesHref}
                onClick={() => handleViewChange(CasesViewMode.Cases)}
                className={cn(
                  "flex size-7 items-center justify-center rounded-l-sm transition-colors",
                  view === CasesViewMode.Cases
                    ? "bg-background text-accent-foreground"
                    : "bg-accent text-muted-foreground hover:bg-muted/50"
                )}
                aria-current={view === CasesViewMode.Cases ? "page" : undefined}
                aria-label="Cases view"
              >
                <SquareStackIcon className="size-3.5" />
              </Link>
            ) : (
              <button
                type="button"
                onClick={() => handleViewChange(CasesViewMode.Cases)}
                className={cn(
                  "flex size-7 items-center justify-center rounded-l-sm transition-colors",
                  view === CasesViewMode.Cases
                    ? "bg-background text-accent-foreground"
                    : "bg-accent text-muted-foreground hover:bg-muted/50"
                )}
                aria-current={view === CasesViewMode.Cases}
                aria-label="Cases view"
              >
                <SquareStackIcon className="size-3.5" />
              </button>
            )}
          </TooltipTrigger>
          <TooltipContent>
            <p>Cases table</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            {customFieldsHref ? (
              <Link
                href={customFieldsHref}
                onClick={() => handleViewChange(CasesViewMode.CustomFields)}
                className={cn(
                  "flex size-7 items-center justify-center rounded-r-sm transition-colors",
                  view === CasesViewMode.CustomFields
                    ? "bg-background text-accent-foreground"
                    : "bg-accent text-muted-foreground hover:bg-muted/50"
                )}
                aria-current={
                  view === CasesViewMode.CustomFields ? "page" : undefined
                }
                aria-label="Custom fields view"
              >
                <BracesIcon className="size-3.5" />
              </Link>
            ) : (
              <button
                type="button"
                onClick={() => handleViewChange(CasesViewMode.CustomFields)}
                className={cn(
                  "flex size-7 items-center justify-center rounded-r-sm transition-colors",
                  view === CasesViewMode.CustomFields
                    ? "bg-background text-accent-foreground"
                    : "bg-accent text-muted-foreground hover:bg-muted/50"
                )}
                aria-current={view === CasesViewMode.CustomFields}
                aria-label="Custom fields view"
              >
                <BracesIcon className="size-3.5" />
              </button>
            )}
          </TooltipTrigger>
          <TooltipContent>
            <p>Custom fields</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}
