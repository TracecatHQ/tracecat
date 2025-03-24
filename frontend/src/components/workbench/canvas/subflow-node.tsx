import React, {
  CSSProperties,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { NodeProps, useReactFlow } from "@xyflow/react"
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react"
import YAML from "yaml"

import { useAction } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import {
  expandSubflowGraph,
  getSubflowNodeDimensions,
  SubflowNodeType,
} from "@/lib/workbench"
import { useSubflow } from "@/hooks/use-subflow"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import {
  ActionSoruceSuccessHandle,
  ActionSourceErrorHandle,
  ActionTargetHandle,
  SubflowTriggerHandle,
} from "@/components/workbench/canvas/custom-handle"
import { SubflowLink } from "@/components/workbench/canvas/subflows"

// Define padding constants
const PADDING = {
  top: 120, // Space for the header
  sides: 50, // Padding on left/right
  bottom: 50, // Padding on bottom
}

// Replace the fixed DIMENSIONS with minimum dimensions
const MIN_DIMENSIONS = {
  width: 300,
  height: 100,
} as const

export default React.memo(function SubflowNode({
  id,
  selected,
}: NodeProps<SubflowNodeType>) {
  const { workflowId, workspaceId, reactFlow } = useWorkflowBuilder()
  const rf = useReactFlow()

  const hasSubNodes = useMemo(() => {
    return reactFlow.getNodes().some((node) => node.parentId === id)
  }, [reactFlow, id])
  const [isExpanded, setIsExpanded] = useState(hasSubNodes)

  const { action } = useAction(id, workspaceId, workflowId!)
  const incomingEdges = reactFlow
    .getEdges()
    .filter((edge) => edge.target === id)

  const actionInputs = useMemo<{
    workflow_id?: string
    workflow_alias?: string
  }>(() => {
    try {
      return action?.inputs
        ? YAML.parse(action.inputs, {
            schema: "core",
            strict: true,
            uniqueKeys: true,
          })
        : {}
    } catch (error) {
      console.error("Failed to parse action inputs:", error)
      toast({
        title: "Invalid action configuration",
        description: "Please ensure that the action inputs are valid YAML",
      })
      return {}
    }
  }, [action])

  const { data: subflow } = useSubflow({
    workflowId: actionInputs?.workflow_id,
    workflowAlias: actionInputs?.workflow_alias,
  })

  const onExpand = useCallback(() => {
    // Only expand if there are nodes to expand
    if (!subflow) return

    // Update the toggle state first for immediate UI feedback
    setIsExpanded(true)

    // Current nodes in the main flow
    const expandedFlow = expandSubflowGraph({
      nodes: reactFlow.getNodes(),
      edges: reactFlow.getEdges(),
      workflow: subflow,
      subflowNodeId: id,
    })
    reactFlow.setNodes(expandedFlow.nodes)
    reactFlow.setEdges(expandedFlow.edges)
  }, [reactFlow, id, subflow])

  const onCollapse = useCallback(() => {
    // Update the toggle state first for immediate UI feedback
    setIsExpanded(false)

    // Remove all nodes that are children of the subflow node
    reactFlow.setNodes((currNodes) =>
      currNodes.filter((node) => node.parentId !== id)
    )
  }, [reactFlow, id])

  // Calculate container dimensions based on child nodes
  const calculateContainerDimensions = useCallback(() => {
    const childNodes = reactFlow
      .getNodes()
      .filter((node) => node.parentId === id)

    if (childNodes.length === 0) {
      return {
        width: MIN_DIMENSIONS.width,
        height: MIN_DIMENSIONS.height,
      }
    }

    // Find the furthest points of child nodes
    const positions = childNodes.map((node) => ({
      right: (node.position.x || 0) + (node.width || 150),
      bottom: (node.position.y || 0) + (node.height || 40),
    }))

    const maxRight = Math.max(...positions.map((p) => p.right))
    const maxBottom = Math.max(...positions.map((p) => p.bottom))

    // Add padding for better visual appearance
    return {
      width: Math.max(maxRight + PADDING.sides * 2, MIN_DIMENSIONS.width),
      height: Math.max(maxBottom + PADDING.bottom, MIN_DIMENSIONS.height),
    }
  }, [reactFlow, id])

  // Style for expanded state with dynamic dimensions
  const expandedStyle = useMemo<CSSProperties>(() => {
    const containerDims = calculateContainerDimensions()
    return {
      width: `${containerDims.width}px`,
      height: `${containerDims.height}px`,
      minWidth: `${containerDims.width}px`,
      minHeight: `${containerDims.height}px`,
      padding: 0,
      transition: "all 0.3s ease-in-out",
    }
  }, [calculateContainerDimensions])

  // Style for collapsed state
  const collapsedStyle = useMemo<CSSProperties>(
    () => ({
      width: `${MIN_DIMENSIONS.width}px`,
      height: `${MIN_DIMENSIONS.height}px`,
      minWidth: `${MIN_DIMENSIONS.width}px`,
      minHeight: `${MIN_DIMENSIONS.height}px`,
      padding: 0,
      transition: "all 0.3s ease-in-out",
    }),
    []
  )

  const hasSubflow = Boolean(subflow)

  // Update node dimensions when child nodes change
  useEffect(() => {
    if (isExpanded) {
      const handleNodesChange = () => {
        // Update the node dimensions based on child nodes
        rf.setNodes((nodes) =>
          nodes.map((node) =>
            node.id === id
              ? {
                  ...node,
                  style: {
                    ...node.style,
                    ...expandedStyle,
                  },
                }
              : node
          )
        )
      }

      // Set initial dimensions
      handleNodesChange()

      // Watch for changes to nodes that are children of this subflow
      const childNodeIds = rf
        .getNodes()
        .filter((node) => node.parentId === id)
        .map((node) => node.id)

      // Set up an interval to periodically check if dimensions need updating
      // This is a simple solution to avoid complex store subscriptions
      const interval = setInterval(() => {
        if (childNodeIds.length > 0) {
          handleNodesChange()
        }
      }, 500)

      // Cleanup
      return () => {
        clearInterval(interval)
      }
    }
  }, [rf, id, isExpanded, expandedStyle])

  // Get dimensions for padding calculations
  const dimensions = useMemo(() => {
    const containerDims = calculateContainerDimensions()
    return getSubflowNodeDimensions(
      MIN_DIMENSIONS.width,
      MIN_DIMENSIONS.height,
      containerDims.width,
      containerDims.height
    )
  }, [calculateContainerDimensions])

  return (
    <Card
      className={cn(
        "size-full",
        "border border-emerald-400",
        "relative flex flex-col rounded-md bg-emerald-300/10",
        "float-left origin-center transition-all duration-200 ease-in-out",
        isExpanded ? "overflow-visible" : "overflow-hidden"
      )}
      style={isExpanded ? expandedStyle : collapsedStyle}
    >
      <div className="z-10 flex items-center justify-between bg-emerald-300/20 p-3">
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            className="bg-white text-xs text-muted-foreground"
          >
            {subflow?.alias || "Subflow"}
          </Badge>
          <SubflowLink
            workspaceId={workspaceId}
            subflowId={subflow?.id}
            subflowAlias={subflow?.alias ?? undefined}
          />
        </div>
        <div className="flex items-center gap-2">
          <Switch
            className={cn(
              hasSubflow &&
                "data-[state=checked]:bg-emerald-400 data-[state=unchecked]:bg-emerald-200"
            )}
            checked={isExpanded && hasSubflow}
            onCheckedChange={(checked) => (checked ? onExpand() : onCollapse())}
            disabled={!hasSubflow}
          >
            {isExpanded ? (
              <ChevronDownIcon className="size-4" />
            ) : (
              <ChevronRightIcon className="size-4" />
            )}
          </Switch>
        </div>
      </div>

      {/* React Flow will automatically render child nodes in this container */}
      <div className="relative grow">
        <ActionTargetHandle
          join_strategy={action?.control_flow?.join_strategy}
          indegree={incomingEdges.length}
        />
        {isExpanded && <SubflowTriggerHandle type="source" />}
        <ActionSoruceSuccessHandle type="source" />
        <ActionSourceErrorHandle type="source" />
      </div>
    </Card>
  )
})
