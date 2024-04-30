"use client"

import { DragEvent, useCallback } from "react"
import { useWorkflowBuilder } from "@/providers/builder"

import { ActionType } from "@/types/schemas"
import { createAction } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { AvailabilityBadge } from "@/components/badges"
import { ActionNodeType } from "@/components/workspace/canvas/action-node"
import { ActionTile } from "@/components/workspace/catalog/action-tiles-schema"

interface ActionTilesProps {
  isCollapsed: boolean
  tiles: ActionTile[]
}
const ACTION_NODE_TAG = "action" as const

export function ActionCatalog({ tiles, isCollapsed }: ActionTilesProps) {
  const { workflowId, selectedNodeId, setNodes, setEdges, getNode } =
    useWorkflowBuilder()
  const handleTileClick = useCallback(
    async (type?: ActionType, title?: string) => {
      const selectedNode = getNode(selectedNodeId ?? "")
      if (!type || !selectedNode || !workflowId || !title) {
        console.error("Missing required data to create action")
        return
      }
      // Proceed to add this action as a child of the selected node

      const newNodeData = {
        type: type,
        title: title || `${type} Action`,
        status: "offline",
        isConfigured: false,
        numberOfEvents: 0,
      }
      const actionId = await createAction(
        newNodeData.type,
        newNodeData.title,
        workflowId
      )

      const newNode = {
        id: actionId,
        type: ACTION_NODE_TAG,
        position: {
          x: selectedNode.position.x,
          y: selectedNode.position.y + 200,
        },
        data: newNodeData,
      } as ActionNodeType

      setNodes((prevNodes) =>
        prevNodes
          .map((n) => ({ ...n, selected: false }))
          .concat({ ...newNode, selected: true })
      )

      // Create an edge between the two nodes
      setEdges((eds) => [
        ...eds,
        {
          id: `${selectedNode.id}-${newNode.id}`,
          source: selectedNode.id,
          target: newNode.id,
        },
      ])
    },
    [selectedNodeId]
  )
  const onDragStart = (event: DragEvent<HTMLDivElement>, tile: ActionTile) => {
    const actionNodeData = {
      type: tile.type,
      title: tile.title || `${tile.type} Action`,
      status: "offline",
      isConfigured: false,
      numberOfEvents: 0,
    }
    event.dataTransfer.setData("application/reactflow", ACTION_NODE_TAG)
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify(actionNodeData)
    )
    event.dataTransfer.effectAllowed = "move"
  }

  return (
    <div
      data-collapsed={isCollapsed}
      className="group flex flex-col gap-4 p-2 data-[collapsed=true]:py-2"
    >
      <nav className="grid px-2 group-[[data-collapsed=true]]:justify-center group-[[data-collapsed=true]]:px-2">
        {tiles.map((tile, index) => {
          const {
            type,
            variant,
            title,
            icon: TileIcon,
            hierarchy,
            availability,
          } = tile
          return isCollapsed ? (
            <Tooltip key={index} delayDuration={0}>
              <TooltipTrigger asChild>
                <div
                  className={cn(
                    buttonVariants({ variant: variant, size: "icon" }),
                    "h-9 w-9",
                    variant === "default" &&
                      "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white",
                    hierarchy === "group" || availability === "comingSoon"
                      ? "hover:cursor-default hover:bg-transparent"
                      : "hover:cursor-grab",
                    availability === "comingSoon" && "opacity-70"
                  )}
                  draggable={
                    hierarchy !== "group" && availability !== "comingSoon"
                  }
                  onDragStart={(event) => onDragStart(event, tile)}
                  onClick={() => {
                    if (!availability) {
                      handleTileClick(type, title)
                    }
                  }}
                >
                  <TileIcon className="h-4 w-4" />
                  <span className="sr-only">{type}</span>
                </div>
              </TooltipTrigger>
              <TooltipContent side="right" className="flex items-center gap-4">
                {type?.startsWith("llm.") && "AI "}
                {title}
                {availability && (
                  <span className="flex grow justify-end">
                    <AvailabilityBadge
                      className="text-xs"
                      availability={availability}
                    />
                  </span>
                )}
              </TooltipContent>
            </Tooltip>
          ) : (
            <Tooltip
              key={index}
              delayDuration={0}
              disableHoverableContent={!availability}
            >
              <TooltipTrigger asChild>
                <div
                  className={cn(
                    hierarchy === "groupItem" &&
                      "ml-5 border-l border-zinc-300 pl-1"
                  )}
                >
                  <div
                    key={index}
                    className={cn(
                      buttonVariants({ variant: variant, size: "sm" }),
                      "justify-start space-x-1",
                      variant === "default" &&
                        "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white",
                      hierarchy === "group" || availability === "comingSoon"
                        ? "hover:cursor-default hover:bg-transparent"
                        : "hover:cursor-grab",
                      availability === "comingSoon" && "opacity-50"
                    )}
                    draggable={
                      hierarchy !== "group" && availability !== "comingSoon"
                    }
                    onDragStart={(event) => onDragStart(event, tile)}
                    onClick={() => {
                      if (!availability) {
                        handleTileClick(type, title)
                      }
                    }}
                  >
                    <TileIcon className="mr-2 h-4 w-4" />
                    <span>{title}</span>
                  </div>
                </div>
              </TooltipTrigger>
              {availability && (
                <TooltipContent side="right" className="bg-transparent">
                  <AvailabilityBadge
                    className="h-5  text-xs"
                    availability={availability}
                  />
                </TooltipContent>
              )}
            </Tooltip>
          )
        })}
      </nav>
    </div>
  )
}
