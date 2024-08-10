"use client"

import { DragEvent } from "react"

import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getIcon } from "@/components/icons"
import { TriggerTypename } from "@/components/workbench/canvas/trigger-node"

const onDragStart = (event: DragEvent<HTMLDivElement>) => {
  event.dataTransfer.setData("application/reactflow", TriggerTypename)
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({
      type: TriggerTypename,
      title: "Trigger",
      status: "offline",
      isConfigured: false,
      webhooks: [],
      schedules: [],
    })
  )
  event.dataTransfer.effectAllowed = "move"
}

export function TriggerCatalog({ isCollapsed }: { isCollapsed: boolean }) {
  return (
    <div
      data-collapsed={isCollapsed}
      className="group flex flex-col gap-4 p-2 data-[collapsed=true]:py-2"
    >
      <nav className="grid px-2 group-[[data-collapsed=true]]:justify-center group-[[data-collapsed=true]]:px-2">
        <Tooltip delayDuration={0}>
          <TooltipTrigger asChild>
            <div
              className={cn(
                buttonVariants({
                  variant: "ghost",
                  size: isCollapsed ? "icon" : "sm",
                }),
                "hover:cursor-default hover:bg-transparent",
                isCollapsed ? "size-9" : "w-full justify-start gap-2"
              )}
              onDragStart={(event) => onDragStart(event)}
            >
              {getIcon(TriggerTypename, { className: "size-5" })}
              {!isCollapsed && <span>Trigger</span>}
            </div>
          </TooltipTrigger>
          <TooltipContent
            side="right"
            className={cn(
              "flex items-center gap-4 rounded-lg p-2 shadow-lg",
              !isCollapsed && "hidden"
            )}
          >
            Trigger
          </TooltipContent>
        </Tooltip>
      </nav>
    </div>
  )
}
