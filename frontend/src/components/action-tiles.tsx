"use client"

import { DragEvent } from "react"
import { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

interface ActionTilesProps {
  isCollapsed: boolean
  tiles: {
    title: string
    label?: string
    icon: LucideIcon
    variant: "default" | "ghost"
  }[]
}

export function ActionTiles({ tiles, isCollapsed }: ActionTilesProps) {
  
  const onDragStart = (
    event: DragEvent<HTMLDivElement>,
    tile: { title: string; label?: string; icon: LucideIcon; variant: "default" | "ghost" }
  ) => {

    const actionNodeData = {
      title: tile.title,
      name: tile.label || `${tile.title} Action`,
      status: "offline",
      numberOfEvents: 0
    };
    event.dataTransfer.setData("application/reactflow", "action")
    event.dataTransfer.setData("application/json", JSON.stringify(actionNodeData))
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      data-collapsed={isCollapsed}
      className="group flex flex-col gap-4 py-2 data-[collapsed=true]:py-2"
    >
      <nav className="grid gap-1 px-2 group-[[data-collapsed=true]]:justify-center group-[[data-collapsed=true]]:px-2">
        {tiles.map((tile, index) =>
          isCollapsed ? (
            <Tooltip key={index} delayDuration={0}>
              <TooltipTrigger asChild>
                <div
                  className={cn(
                    buttonVariants({ variant: tile.variant, size: "icon" }),
                    "h-9 w-9",
                    tile.variant === "default" &&
                      "dark:bg-muted dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-white"
                  )}
                  draggable
                  onMouseOver={(e) => e.currentTarget.style.cursor = "grab"}
                  onMouseOut={(e) => e.currentTarget.style.cursor = ""}
                  onDragStart={(event) => onDragStart(event, tile)}
                >
                  <tile.icon className="h-4 w-4" />
                  <span className="sr-only">{tile.title}</span>
                </div>
              </TooltipTrigger>
              <TooltipContent side="right" className="flex items-center gap-4">
                {tile.title}
                {tile.label && (
                  <span className="ml-auto text-muted-foreground">
                    {tile.label}
                  </span>
                )}
              </TooltipContent>
            </Tooltip>
          ) : (
            <div
              key={index}
              className={cn(
                buttonVariants({ variant: tile.variant, size: "sm" }),
                tile.variant === "default" &&
                  "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white",
                "justify-start"
              )}
              draggable
              onMouseOver={(e) => e.currentTarget.style.cursor = "grab"}
              onMouseOut={(e) => e.currentTarget.style.cursor = ""}
              onDragStart={(event) => onDragStart(event, tile)}
            >
              <tile.icon className="mr-2 h-4 w-4" />
              {tile.title}
              {tile.label && (
                <span
                  className={cn(
                    "ml-auto",
                    tile.variant === "default" &&
                      "text-background dark:text-white"
                  )}
                >
                  {tile.label}
                </span>
              )}
            </div>
          )
        )}
      </nav>
    </div>
  )
}
