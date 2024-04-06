import React, { useCallback } from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useSession } from "@/providers/session"
import {
  BellDotIcon,
  ChevronDownIcon,
  CircleIcon,
  Copy,
  Delete,
  EyeIcon,
  ScanSearchIcon,
  Sparkles,
} from "lucide-react"
import { Handle, Node, NodeProps, Position, useNodeId } from "reactflow"

import { IntegrationPlatform, IntegrationType } from "@/types/schemas"
import { cn, copyToClipboard, slugify, undoSlugify } from "@/lib/utils"
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
import { useToast } from "@/components/ui/use-toast"
import { Integrations } from "@/components/icons"

export type IntegrationNodeType = Node<IntegrationNodeData>
export interface IntegrationNodeData {
  type: IntegrationType
  title: string
  status: "online" | "offline"
  isConfigured: boolean
  numberOfEvents: number
  // Generic metadata
}
const handleStyle = { width: 8, height: 8 }
export default React.memo(function IntegrationNode({
  data: { title, isConfigured, numberOfEvents, type },
  selected,
}: NodeProps<IntegrationNodeData>) {
  const id = useNodeId()
  const session = useSession()
  const { workflowId, getNode, reactFlow } = useWorkflowBuilder()
  const { toast } = useToast()
  const platform = type.split(".")[1] as IntegrationPlatform
  const Icon = Integrations[platform]
  const isConfiguredMessage = isConfigured ? "ready" : "missing inputs"

  const handleCopyToClipboard = useCallback(() => {
    const slug = slugify(title)
    copyToClipboard({
      value: slug,
      message: `JSONPath copied to clipboard`,
    })
    toast({
      title: "Copied action tile slug",
      description: `The slug ${slug} has been copied to your clipboard.`,
    })
  }, [title, toast])

  const handleDeleteNode = useCallback(async () => {
    if (!workflowId || !id) {
      return
    }
    const node = getNode(id)
    if (!node) {
      console.error("Could not find node with ID", id)
      return
    }
    try {
      reactFlow.deleteElements({ nodes: [node] })
      toast({
        title: "Deleted action node",
        description: "Successfully deleted action node.",
      })
    } catch (error) {
      console.error("An error occurred while deleting Action nodes:", error)
      toast({
        title: "Error deleting action node",
        description: "Failed to delete action node.",
      })
    }
  }, [session, id, toast])

  return (
    <Card className={cn(selected && "shadow-xl drop-shadow-xl")}>
      <CardHeader className="grid p-4 px-5">
        <div className="flex w-full items-center space-x-4">
          <Icon className="mr-2 size-6 shrink-0 rounded-sm" />
          <div className="flex w-full flex-1 justify-between space-x-12">
            <div className="flex flex-col">
              <CardTitle className="flex w-full items-center justify-between text-sm font-medium leading-none">
                <div className="flex w-full">
                  {title}
                  {type.startsWith("llm.") && (
                    <Sparkles className="ml-2 h-3 w-3 fill-yellow-500 text-yellow-500" />
                  )}
                </div>
              </CardTitle>
              <CardDescription className="mt-1 text-sm capitalize text-muted-foreground">
                {undoSlugify(platform)}
              </CardDescription>
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="m-0 h-6 w-6 p-0">
                  <ChevronDownIcon className="m-1 h-4 w-4 text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={handleCopyToClipboard}>
                  <Copy className="mr-2 h-4 w-4" />
                  <span className="text-xs">Copy JSONPath</span>
                </DropdownMenuItem>
                <DropdownMenuItem disabled>
                  <ScanSearchIcon className="mr-2 h-4 w-4" />
                  <span className="text-xs">Search events</span>
                </DropdownMenuItem>
                <DropdownMenuItem disabled>
                  <EyeIcon className="mr-2 h-4 w-4" />
                  <span className="text-xs">View logs</span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={handleDeleteNode}>
                  <Delete className="mr-2 h-4 w-4 text-red-600" />
                  <span className="text-xs text-red-600">Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </CardHeader>

      <CardContent className="pb-4 pl-5 pr-5 pt-0">
        <div className="flex space-x-4 text-xs text-muted-foreground">
          <div className="flex items-center">
            <CircleIcon
              className={cn(
                "mr-1 h-3 w-3",
                isConfigured
                  ? "fill-green-600 text-green-600"
                  : "fill-gray-400 text-gray-400"
              )}
            />
            <span className="capitalize">{isConfiguredMessage}</span>
          </div>
          <div className="flex items-center">
            <BellDotIcon className="mr-1 h-3 w-3" />
            <span>{numberOfEvents}</span>
          </div>
        </div>
      </CardContent>

      <Handle
        type="target"
        position={Position.Top}
        className="w-16 !bg-gray-500"
        style={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        className="w-16 !bg-gray-500"
        style={handleStyle}
      />
    </Card>
  )
})
