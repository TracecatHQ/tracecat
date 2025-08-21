"use client"

import { FolderIcon, LayoutIcon, TagIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export enum ViewMode {
  Tags = "tags",
  Folders = "folders",
}

interface FolderViewToggleProps {
  view: ViewMode
  onViewChange?: (view: ViewMode) => void
  variant?: "icon" | "dropdown" | "minimal"
  className?: string
}

export function FolderViewToggle({
  view,
  onViewChange,
  variant = "minimal",
  className,
}: FolderViewToggleProps) {
  const handleViewChange = (view: ViewMode) => {
    onViewChange?.(view)
  }

  // Icon-only toggle with tooltip
  if (variant === "icon") {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              onClick={() =>
                handleViewChange(
                  view === ViewMode.Folders ? ViewMode.Tags : ViewMode.Folders
                )
              }
              className="size-8 border-input text-muted-foreground hover:bg-muted/50"
              aria-label="Toggle view"
            >
              {view === ViewMode.Folders ? (
                <FolderIcon className="size-4" />
              ) : (
                <TagIcon className="size-4" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>
              Switch to{" "}
              {view === ViewMode.Folders ? ViewMode.Tags : ViewMode.Folders}{" "}
              view
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  // Dropdown toggle
  if (variant === "dropdown") {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className={cn("gap-1", className)}
          >
            <LayoutIcon className="mr-1 size-3.5" />
            View
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onClick={() => handleViewChange(ViewMode.Folders)}
            className={cn(view === ViewMode.Folders && "bg-muted")}
          >
            <FolderIcon className="mr-2 size-4" />
            <span>Folder view</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => handleViewChange(ViewMode.Tags)}
            className={cn(view === ViewMode.Tags && "bg-muted")}
          >
            <TagIcon className="mr-2 size-4" />
            <span>Tags view</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    )
  }

  // Minimal toggle (default)
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
              onClick={() => handleViewChange(ViewMode.Tags)}
              className={cn(
                "flex size-7 items-center justify-center rounded-l-sm transition-colors",
                view === ViewMode.Tags
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={view === ViewMode.Tags}
              aria-label="Tags view"
            >
              <TagIcon className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Switch to tags view</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => handleViewChange(ViewMode.Folders)}
              className={cn(
                "flex size-7 items-center justify-center rounded-r-sm transition-colors",
                view === ViewMode.Folders
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={view === ViewMode.Folders}
              aria-label="Folder view"
            >
              <FolderIcon className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Switch to folder view</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}
