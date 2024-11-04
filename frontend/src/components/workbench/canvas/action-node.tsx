import React, { useCallback } from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import {
  ChevronDownIcon,
  CircleCheckBigIcon,
  LayoutListIcon,
  Trash2Icon,
} from "lucide-react"
import { Node, NodeProps, useEdges } from "reactflow"

import { useAction } from "@/lib/hooks"
import { cn, slugify } from "@/lib/utils"
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
import { useToast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
import { getIcon } from "@/components/icons"
import {
  ActionSoruceSuccessHandle,
  ActionSourceErrorHandle,
  ActionTargetHandle,
} from "@/components/workbench/canvas/custom-handle"

export interface ActionNodeData {
  type: string // alias for key
  title: string
  namespace: string
  status: "online" | "offline"
  isConfigured: boolean
  numberOfEvents: number
}
export type ActionNodeType = Node<ActionNodeData>

export default React.memo(function ActionNode({
  data: { title, isConfigured, type: key },
  selected,
  id,
}: NodeProps<ActionNodeData>) {
  const { workflowId, getNode, workspaceId, reactFlow } = useWorkflowBuilder()
  const { toast } = useToast()
  const isConfiguredMessage = isConfigured ? "ready" : "missing inputs"
  // SAFETY: Node only exists if it's in the workflow
  const { action } = useAction(id, workspaceId, workflowId!)

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

  return (
    <Card className={cn("min-w-72", selected && "shadow-xl drop-shadow-xl")}>
      <CardHeader className="p-4">
        <div className="flex w-full items-center space-x-4">
          {getIcon(key, {
            className: "size-10 p-2",
          })}

          <div className="flex w-full flex-1 justify-between space-x-12">
            <div className="flex flex-col">
              <CardTitle className="flex w-full items-center space-x-2 text-xs font-medium leading-none">
                <span>{title}</span>
                <CopyButton
                  value={`\$\{\{ ACTIONS.${slugify(title)}.result \}\}`}
                  toastMessage="Copied action reference to clipboard"
                  tooltipMessage="Copy action reference"
                />
              </CardTitle>
              <CardDescription className="mt-2 text-xs text-muted-foreground">
                {key}
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
            <span className="text-xs capitalize">{isConfiguredMessage}</span>
          </div>
        </div>
      </CardContent>

      <ActionTargetHandle
        join_strategy={action?.control_flow?.join_strategy}
        indegree={incomingEdges.length}
      />
      <ActionSoruceSuccessHandle type="source" />
      <ActionSourceErrorHandle type="source" />
    </Card>
  )
})
