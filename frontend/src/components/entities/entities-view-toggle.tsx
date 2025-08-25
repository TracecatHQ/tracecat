"use client"

import { BracesIcon, Link as LinkIcon, Rows } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

export enum EntitiesViewMode {
  Fields = "fields",
  Relations = "relations",
  Records = "records",
}

interface EntitiesViewToggleProps {
  className?: string
}

export function EntitiesViewToggle({ className }: EntitiesViewToggleProps) {
  const pathname = usePathname()
  const { workspaceId } = useWorkspace()

  const basePath = workspaceId
    ? `/workspaces/${workspaceId}/entities`
    : "/workspaces/unknown/entities"

  // Derive active view from props or pathname
  const active: EntitiesViewMode = pathname?.includes("/entities/records")
    ? EntitiesViewMode.Records
    : pathname?.includes("/entities/relations")
      ? EntitiesViewMode.Relations
      : EntitiesViewMode.Fields

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
            <Link
              href={basePath}
              className={cn(
                "flex size-7 items-center justify-center rounded-l-sm transition-colors",
                active === EntitiesViewMode.Fields
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={
                active === EntitiesViewMode.Fields ? "page" : undefined
              }
              aria-label="Fields view"
            >
              <BracesIcon className="size-3.5" />
            </Link>
          </TooltipTrigger>
          <TooltipContent>
            <p>Fields</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Link
              href={`${basePath}/relations`}
              className={cn(
                "flex size-7 items-center justify-center transition-colors",
                active === EntitiesViewMode.Relations
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={
                active === EntitiesViewMode.Relations ? "page" : undefined
              }
              aria-label="Relations view"
            >
              <LinkIcon className="size-3.5" />
            </Link>
          </TooltipTrigger>
          <TooltipContent>
            <p>Relations</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Link
              href={`${basePath}/records`}
              className={cn(
                "flex size-7 items-center justify-center rounded-r-sm transition-colors",
                active === EntitiesViewMode.Records
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={
                active === EntitiesViewMode.Records ? "page" : undefined
              }
              aria-label="Records view"
            >
              <Rows className="size-3.5" />
            </Link>
          </TooltipTrigger>
          <TooltipContent>
            <p>Records</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}
