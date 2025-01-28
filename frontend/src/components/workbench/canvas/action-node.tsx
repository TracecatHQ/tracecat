import React, { useCallback } from "react"
import Link from "next/link"
import { useWorkflowBuilder } from "@/providers/builder"
import {
  AlertTriangleIcon,
  ChevronDownIcon,
  CircleCheckBigIcon,
  LayoutListIcon,
  SquareArrowOutUpRightIcon,
  Trash2Icon,
} from "lucide-react"
import { Node, NodeProps, useEdges } from "reactflow"
import YAML from "yaml"

import { useAction, useWorkflowManager } from "@/lib/hooks"
import { cn, isEmptyObjectOrNullish, slugify } from "@/lib/utils"
import { CHILD_WORKFLOW_ACTION_TYPE } from "@/lib/workflow"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
import { getIcon } from "@/components/icons"
import { AlertNotification } from "@/components/notifications"
import {
  ActionSoruceSuccessHandle,
  ActionSourceErrorHandle,
  ActionTargetHandle,
} from "@/components/workbench/canvas/custom-handle"

/**
 * Represents the data structure for an Action Node
 * @deprecated Previous version contained additional fields that are no longer used.
 * Extra fields in existing data structures will be ignored.
 */
export interface ActionNodeData {
  type: string // alias for key
  isConfigured: boolean

  // Allow any additional properties from legacy data
  [key: string]: unknown
}

export type ActionNodeType = Node<ActionNodeData>

export default React.memo(function ActionNode({
  selected,
  id,
}: NodeProps<ActionNodeData>) {
  const { workflowId, getNode, workspaceId, reactFlow } = useWorkflowBuilder()
  const { toast } = useToast()
  // SAFETY: Node only exists if it's in the workflow
  const { action, actionIsLoading } = useAction(id, workspaceId, workflowId!)
  const isConfigured = !isEmptyObjectOrNullish(action?.inputs)

  const handleDeleteNode = useCallback(async () => {
    try {
      if (!workflowId || !id) {
        throw new Error("Missing required data to delete node")
      }
      const node = getNode(id)
      if (!node) {
        console.error("Could not find node with ID", id)
        throw new Error("Could not find node to delete")
      }
      reactFlow.deleteElements({ nodes: [node] })
    } catch (error) {
      console.error("An error occurred while deleting Action nodes:", error)
      toast({
        title: "Error deleting action node",
        description: "Failed to delete action node.",
        variant: "destructive",
      })
    }
  }, [id, toast])

  // Add this to track incoming edges
  const edges = useEdges()
  const incomingEdges = edges.filter((edge) => edge.target === id)
  const isChildWorkflow = action?.type === CHILD_WORKFLOW_ACTION_TYPE
  const actionInputsObj = action?.inputs ? YAML.parse(action?.inputs) : {}
  const childWorkflowId = actionInputsObj?.workflow_id
    ? String(actionInputsObj?.workflow_id)
    : undefined
  const childWorkflowAlias = actionInputsObj?.workflow_alias
    ? String(actionInputsObj?.workflow_alias)
    : undefined

  // Create a skeleton loading state within the card frame
  const renderContent = () => {
    if (actionIsLoading) {
      return (
        <>
          <CardHeader className="p-4">
            <div className="flex w-full items-center space-x-4">
              <Skeleton className="size-10 rounded-full" />
              <div className="flex w-full flex-1 justify-between space-x-12">
                <div className="flex flex-col space-y-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-24" />
                </div>
                <Skeleton className="size-6" />
              </div>
            </div>
          </CardHeader>
          <Separator />
          <CardContent className="p-4 py-2">
            <div className="grid grid-cols-2 space-x-4 text-xs text-muted-foreground">
              <div className="flex items-center space-x-2">
                <Skeleton className="size-4" />
                <Skeleton className="h-3 w-16" />
              </div>
            </div>
          </CardContent>
        </>
      )
    }

    if (!action) {
      return (
        <div className="p-4">
          <AlertNotification
            variant="warning"
            title="Could not load action"
            message="Please try again."
          />
        </div>
      )
    }

    return (
      <>
        <CardHeader className="p-4">
          <div className="flex w-full items-center space-x-4">
            {getIcon(action.type, {
              className: "size-10 p-2",
            })}

            <div className="flex w-full flex-1 justify-between space-x-12">
              <div className="flex flex-col">
                <CardTitle className="flex w-full items-center space-x-2 text-xs font-medium leading-none">
                  <span>{action.title}</span>
                  <CopyButton
                    value={`\$\{\{ ACTIONS.${slugify(action.title)}.result \}\}`}
                    toastMessage="Copied action reference to clipboard"
                    tooltipMessage="Copy action reference"
                  />
                </CardTitle>
                <CardDescription className="mt-2 text-xs text-muted-foreground">
                  {action.type}
                </CardDescription>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="m-0 size-6 p-0">
                    <ChevronDownIcon className="m-1 size-4 text-muted-foreground" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={handleDeleteNode}>
                    <Trash2Icon className="mr-2 size-4 text-red-600" />
                    <span className="text-xs text-red-600">Delete</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </CardHeader>
        <Separator />
        <CardContent className="p-4 py-2">
          <div className="grid grid-cols-2 space-x-4 text-xs text-muted-foreground">
            <div className="flex items-center space-x-2">
              {isConfigured ? (
                <CircleCheckBigIcon className="size-4 text-emerald-500" />
              ) : (
                <LayoutListIcon className="size-4 text-gray-400" />
              )}
              <span className="text-xs capitalize">
                {isConfigured ? "Ready" : "Missing inputs"}
              </span>
            </div>
            {isChildWorkflow && (
              <ChildWorkflowLink
                workspaceId={workspaceId}
                childWorkflowId={childWorkflowId}
                childWorkflowAlias={childWorkflowAlias}
              />
            )}
          </div>
        </CardContent>
      </>
    )
  }

  return (
    <Card className={cn("min-w-72", selected && "shadow-xl drop-shadow-xl")}>
      {renderContent()}
      <ActionTargetHandle
        join_strategy={action?.control_flow?.join_strategy}
        indegree={incomingEdges.length}
      />
      <ActionSoruceSuccessHandle type="source" />
      <ActionSourceErrorHandle type="source" />
    </Card>
  )
})

function ChildWorkflowLink({
  workspaceId,
  childWorkflowId,
  childWorkflowAlias,
}: {
  workspaceId: string
  childWorkflowId?: string
  childWorkflowAlias?: string
}) {
  const { workflows } = useWorkflowManager()
  const { setSelectedNodeId } = useWorkflowBuilder()
  const childIdFromAlias = workflows?.find(
    (w) => w.alias === childWorkflowAlias
  )?.id

  const handleClearSelection = () => {
    setSelectedNodeId(null)
  }

  const inner = () => {
    if (childWorkflowId) {
      return (
        <Link
          href={`/workspaces/${workspaceId}/workflows/${childWorkflowId}`}
          onClick={handleClearSelection}
        >
          <div className="flex items-center gap-1">
            <span className="font-normal">Open workflow</span>
            <SquareArrowOutUpRightIcon className="size-3" />
          </div>
        </Link>
      )
    }
    if (childWorkflowAlias) {
      if (!childIdFromAlias) {
        return (
          <div className="flex items-center gap-1">
            <span className="font-normal">Cannot find workflow by alias</span>
            <AlertTriangleIcon className="size-3 text-red-500" />
          </div>
        )
      }
      return (
        <div className="flex items-center gap-1">
          <Link
            href={`/workspaces/${workspaceId}/workflows/${childIdFromAlias}`}
            onClick={handleClearSelection}
          >
            <div className="flex items-center gap-1">
              <span className="font-mono font-normal tracking-tighter text-foreground/80">
                {childWorkflowAlias}
              </span>
              <SquareArrowOutUpRightIcon className="size-3" />
            </div>
          </Link>
        </div>
      )
    }
    return <span className="font-normal">Missing identifier</span>
  }
  return (
    <div className="flex justify-end">
      <Badge
        variant="outline"
        className="text-foreground/70 hover:cursor-pointer hover:bg-muted-foreground/5"
      >
        {inner()}
      </Badge>
    </div>
  )
}
