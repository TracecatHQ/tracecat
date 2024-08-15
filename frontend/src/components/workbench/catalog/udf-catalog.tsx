"use client"

import { DragEvent, useCallback } from "react"
import { useWorkflowBuilder } from "@/providers/builder"

import { udfConfig } from "@/config/udfs"
import { UDF, useUDFs } from "@/lib/udf"
import { cn } from "@/lib/utils"
import { createAction } from "@/lib/workflow"
import { buttonVariants } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  RFGraphUDFNodeType,
  UDFNodeData,
  UDFNodeType,
} from "@/components/workbench/canvas/udf-node"

export const TOP_LEVEL_GROUP = "__TOP_LEVEL__" as const

export const groupByDisplayGroup = (udfs: UDF[]): Record<string, UDF[]> => {
  const groups = {} as Record<string, UDF[]>
  udfs.forEach((udf) => {
    const displayGroup = (
      udf.metadata?.display_group || TOP_LEVEL_GROUP
    ).toString()
    if (!groups[displayGroup]) {
      groups[displayGroup] = []
    }
    groups[displayGroup].push(udf)
  })
  return groups
}

const onDragStart = (event: DragEvent<HTMLDivElement>, udf: UDF) => {
  event.dataTransfer.setData("application/reactflow", RFGraphUDFNodeType)
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({
      type: udf.key,
      title: udf.metadata?.default_title || udf.key,
      namespace: udf.namespace,
      status: "offline",
      isConfigured: false,
      numberOfEvents: 0,
    })
  )
  event.dataTransfer.effectAllowed = "move"
}

export function UDFCatalog({ isCollapsed }: { isCollapsed: boolean }) {
  const {
    workspaceId,
    workflowId,
    selectedNodeId,
    setNodes,
    setEdges,
    getNode,
  } = useWorkflowBuilder()
  const {
    udfs,
    isLoading: udfsLoading,
    error,
  } = useUDFs(workspaceId, udfConfig.namespaces)

  /**
   * Enables the user to create an action node by clicking on a tile
   */
  const handleTileClick = useCallback(
    async (udf: UDF) => {
      const selectedNode = getNode(selectedNodeId ?? "")
      if (!selectedNode || !workflowId) {
        console.error("Missing required data to create action")
        return
      }

      const newNodeData = {
        type: udf.key,
        title: udf.metadata?.default_title || udf.key,
        namespace: udf.namespace,
        status: "offline",
        isConfigured: false,
        numberOfEvents: 0,
      } as UDFNodeData

      const actionId = await createAction(
        workspaceId,
        newNodeData.type,
        newNodeData.title,
        workflowId
      )

      const newNode = {
        id: actionId,
        type: RFGraphUDFNodeType,
        position: {
          x: selectedNode.position.x,
          y: selectedNode.position.y + (selectedNode.height ?? 200) + 50,
        },
        data: newNodeData,
      } as UDFNodeType

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

  if (error) {
    console.error("Failed to load UDFs", error)
    return <AlertNotification level="error" message="Failed to load UDFs" />
  }
  if (!udfs || udfsLoading) {
    return <CenteredSpinner />
  }

  return (
    <div
      data-collapsed={isCollapsed}
      className="group flex flex-col gap-4 p-2 data-[collapsed=true]:py-2"
    >
      <nav className="grid px-2 group-[[data-collapsed=true]]:justify-center group-[[data-collapsed=true]]:px-2">
        {Object.entries(groupByDisplayGroup(udfs))
          .sort(([groupA], [groupB]) => groupA.localeCompare(groupB))
          .map(([group, udfs], groupIndex) => {
            return (
              <div key={groupIndex}>
                {group !== TOP_LEVEL_GROUP && (
                  <UDFCatalogGroup
                    key={groupIndex}
                    displayName={group}
                    isCollapsed={isCollapsed}
                  />
                )}
                {udfs.map((udf, index) => (
                  <UDFCatalogItem
                    key={index}
                    indent={group === TOP_LEVEL_GROUP ? 0 : 1}
                    udf={udf}
                    isCollapsed={isCollapsed}
                    handleTileClick={handleTileClick}
                  />
                ))}
              </div>
            )
          })}
      </nav>
    </div>
  )
}

function UDFCatalogGroup({
  displayName,
  isCollapsed,
}: {
  displayName: string
  isCollapsed: boolean
}) {
  return (
    <div
      className={cn(
        buttonVariants({
          variant: "ghost",
          size: "sm",
        }),
        isCollapsed ? "hidden" : "w-full justify-start gap-2",
        "hover:cursor-default hover:bg-transparent"
      )}
    >
      {getIcon("group", { className: "size-5" })}
      {!isCollapsed && <span>{displayName}</span>}
    </div>
  )
}

function UDFCatalogItem({
  indent = 0,
  udf,
  isCollapsed,
  handleTileClick,
  isAvailable = true,
}: {
  indent?: number
  udf: UDF
  isCollapsed: boolean
  handleTileClick: (udf: UDF) => void
  isAvailable?: boolean
}) {
  const defaultTitle = (udf.metadata?.default_title || udf.key).toString()
  return (
    <Tooltip delayDuration={0}>
      <TooltipTrigger asChild>
        <div
          className={cn(
            !isCollapsed && indent > 0 && "ml-5 border-l border-zinc-300 pl-1"
          )}
        >
          <div
            className={cn(
              buttonVariants({
                variant: "ghost",
                size: isCollapsed ? "icon" : "sm",
              }),
              "hover:cursor-grab",
              isCollapsed ? "size-9" : "w-full justify-start gap-2"
            )}
            draggable={isAvailable}
            onDragStart={(event) => onDragStart(event, udf)}
            onClick={() => handleTileClick(udf)}
          >
            {getIcon(udf.key, {
              className: "size-5",
              flairsize: "sm",
            })}
            {!isCollapsed && <span>{defaultTitle}</span>}
          </div>
        </div>
      </TooltipTrigger>
      <TooltipContent
        side="right"
        className={cn(
          "flex items-center gap-4 rounded-lg p-2 shadow-lg",
          !isCollapsed && "hidden"
        )}
      >
        {defaultTitle}
      </TooltipContent>
    </Tooltip>
  )
}
