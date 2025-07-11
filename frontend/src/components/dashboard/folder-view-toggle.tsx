"use client"

import { FolderIcon, LayoutIcon, TagIcon } from "lucide-react"
import { useState } from "react"
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
  defaultView?: ViewMode
  onViewChange?: (view: ViewMode) => void
  variant?: "icon" | "dropdown" | "minimal"
  className?: string
}

export function FolderViewToggle({
  defaultView = ViewMode.Tags,
  onViewChange,
  variant = "minimal",
  className,
}: FolderViewToggleProps) {
  const [activeView, setActiveView] = useState<ViewMode>(defaultView)
  const handleViewChange = (view: ViewMode) => {
    setActiveView(view)
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
                  activeView === ViewMode.Folders
                    ? ViewMode.Tags
                    : ViewMode.Folders
                )
              }
              className="size-8 border-input text-muted-foreground hover:bg-muted/50"
              aria-label="Toggle view"
            >
              {activeView === ViewMode.Folders ? (
                <FolderIcon className="size-4" />
              ) : (
                <TagIcon className="size-4" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>
              Switch to{" "}
              {activeView === ViewMode.Folders
                ? ViewMode.Tags
                : ViewMode.Folders}{" "}
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
            className={cn(activeView === ViewMode.Folders && "bg-muted")}
          >
            <FolderIcon className="mr-2 size-4" />
            <span>Folder view</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => handleViewChange(ViewMode.Tags)}
            className={cn(activeView === ViewMode.Tags && "bg-muted")}
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
        "inline-flex items-center rounded-md border border-muted bg-transparent",
        className
      )}
    >
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => handleViewChange(ViewMode.Tags)}
              className={cn(
                "flex size-7 items-center justify-center rounded-r-sm transition-colors",
                activeView === ViewMode.Tags
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={activeView === ViewMode.Tags}
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
              onClick={() => handleViewChange(ViewMode.Folders)}
              className={cn(
                "flex size-7 items-center justify-center rounded-l-sm transition-colors",
                activeView === ViewMode.Folders
                  ? "bg-background text-accent-foreground"
                  : "bg-accent text-muted-foreground hover:bg-muted/50"
              )}
              aria-current={activeView === ViewMode.Folders}
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
