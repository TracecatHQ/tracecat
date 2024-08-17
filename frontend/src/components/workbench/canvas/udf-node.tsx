import React, { useCallback } from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import {
  BellDotIcon,
  ChevronDownIcon,
  CircleCheckBigIcon,
  Copy,
  Delete,
  EyeIcon,
  LayoutListIcon,
  ScanSearchIcon,
} from "lucide-react"
import { Node, NodeProps, Position, useNodeId } from "reactflow"

import { cn, copyToClipboard, slugify } from "@/lib/utils"
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
import { getIcon } from "@/components/icons"
import { CustomFloatingHandle } from "@/components/workbench/canvas/custom-handle"

export interface UDFNodeData {
  type: string // alias for key
  title: string
  namespace: string
  status: "online" | "offline"
  isConfigured: boolean
  numberOfEvents: number
}
export type UDFNodeType = Node<UDFNodeData>
export const RFGraphUDFNodeType = "udf" as const

export default React.memo(function UDFNode({
  data: { title, isConfigured, numberOfEvents, type: key },
  selected,
  sourcePosition,
  targetPosition,
}: NodeProps<UDFNodeData>) {
  const id = useNodeId()
  const { workflowId, getNode, reactFlow } = useWorkflowBuilder()
  const { toast } = useToast()
  const isConfiguredMessage = isConfigured ? "ready" : "missing inputs"

  const handleCopyToClipboard = useCallback(() => {
    const slug = slugify(title)
    const ref = `ACTIONS.${slug}`
    copyToClipboard({
      value: ref,
      message: `Action reference copied to clipboard`,
    })
    toast({
      title: "Copied action node reference",
      description: `The action reference '${ref}' has been copied to your clipboard.`,
    })
  }, [title, toast])

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

  return (
    <Card className={cn("min-w-72", selected && "shadow-xl drop-shadow-xl")}>
      <CardHeader className="p-4">
        <div className="flex w-full items-center space-x-4">
          {getIcon(key, {
            className: "size-10 p-2",
          })}

          <div className="flex w-full flex-1 justify-between space-x-12">
            <div className="flex flex-col">
              <CardTitle className="flex w-full items-center justify-between text-xs font-medium leading-none">
                <div className="flex w-full">{title}</div>
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
                <DropdownMenuItem onClick={handleCopyToClipboard}>
                  <Copy className="mr-2 size-4" />
                  <span className="text-xs">Copy action reference</span>
                </DropdownMenuItem>
                <DropdownMenuItem disabled>
                  <ScanSearchIcon className="mr-2 size-4" />
                  <span className="text-xs">Search events</span>
                </DropdownMenuItem>
                <DropdownMenuItem disabled>
                  <EyeIcon className="mr-2 size-4" />
                  <span className="text-xs">View logs</span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={handleDeleteNode}>
                  <Delete className="mr-2 size-4 text-red-600" />
                  <span className="text-xs text-red-600">Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </CardHeader>
      <Separator />
      <CardContent className="p-4 py-3">
        <div className="grid grid-cols-2 space-x-4 text-xs text-muted-foreground">
          <div className="flex items-center space-x-2">
            {isConfigured ? (
              <CircleCheckBigIcon className="size-4 text-emerald-500" />
            ) : (
              <LayoutListIcon className="size-4 text-gray-400" />
            )}
            <span className="text-xs capitalize">{isConfiguredMessage}</span>
          </div>
          <div className="flex items-center justify-end">
            <BellDotIcon className="mr-2 size-3" />
            <span>{numberOfEvents}</span>
          </div>
        </div>
      </CardContent>

      <CustomFloatingHandle
        type="target"
        position={targetPosition ?? Position.Top}
      />
      <CustomFloatingHandle
        type="source"
        position={sourcePosition ?? Position.Bottom}
      />
    </Card>
  )
})
