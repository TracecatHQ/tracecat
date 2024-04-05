import React, { useCallback } from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useSession } from "@/providers/session"
import {
  BellDotIcon,
  Blend,
  BookText,
  CheckSquare,
  ChevronDownIcon,
  CircleIcon,
  Container,
  Copy,
  Delete,
  EyeIcon,
  FlaskConical,
  GitCompareArrows,
  Globe,
  Languages,
  LucideIcon,
  Mail,
  Regex,
  ScanSearchIcon,
  Send,
  ShieldAlert,
  Sparkles,
  Tags,
  Webhook,
} from "lucide-react"
import { Handle, NodeProps, Position, useNodeId, type Node } from "reactflow"

import { ActionType } from "@/types/schemas"
import { cn, copyToClipboard, slugify } from "@/lib/utils"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
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

export type ActionNodeType = Node<ActionNodeData>
export interface ActionNodeData {
  type: ActionType
  title: string
  status: "online" | "offline"
  isConfigured: boolean
  numberOfEvents: number
  // Generic metadata
}

export const tileIconMapping: Partial<Record<ActionType, LucideIcon>> = {
  webhook: Webhook,
  http_request: Globe,
  data_transform: Blend,
  "condition.compare": GitCompareArrows,
  "condition.regex": Regex,
  "condition.membership": Container,
  open_case: ShieldAlert,
  receive_email: Mail,
  send_email: Send,
  "llm.extract": FlaskConical,
  "llm.label": Tags,
  "llm.translate": Languages,
  "llm.choice": CheckSquare,
  "llm.summarize": BookText,
} as const
export const tileColorMap: Record<string, string> = {
  llm: "bg-amber-100",
  condition: "bg-orange-100",
  data_transform: "bg-cyan-100",
  http_request: "bg-emerald-100",
  open_case: "bg-rose-100",
  receive_email: "bg-purple-100",
  send_email: "bg-lime-100",
  webhook: "bg-indigo-100",
} as const
type TileColorMap = typeof tileColorMap

export function getTileColor(
  type?: string,
  defaultColor: string = "bg-slate-100"
) {
  if (type) {
    // Check for exact matches first
    const typeKey = type as keyof TileColorMap
    if (tileColorMap[typeKey]) {
      return tileColorMap[typeKey]
    }
    // Check for prefix matches
    for (const key in tileColorMap) {
      if (type.startsWith(key)) {
        return tileColorMap[key as keyof TileColorMap]
      }
    }
  }
  return defaultColor
}
const handleStyle = { width: 8, height: 8 }

export default React.memo(function ActionNode({
  data: { type, title, isConfigured, numberOfEvents },
  selected,
}: NodeProps<ActionNodeData>) {
  const id = useNodeId()
  const session = useSession()
  const { workflowId, getNode, reactFlow } = useWorkflowBuilder()
  const tileIcon = tileIconMapping[type] ?? Sparkles
  const isConfiguredMessage = isConfigured ? "ready" : "missing inputs"
  const { toast } = useToast()
  const avatarImageAlt = `${type}-${title}`

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
          <Avatar>
            <AvatarImage className="bg-red-600" src="" alt={avatarImageAlt} />
            <AvatarFallback className={cn(getTileColor(type))}>
              {React.createElement(tileIcon, { className: "h-5 w-5" })}
            </AvatarFallback>
          </Avatar>
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
              <CardDescription className="mt-1 text-sm text-muted-foreground">
                {type}
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
          {/* <div className="flex items-center">
            <CircleIcon
              className={cn(
                "mr-1 h-3 w-3",
                status === "online"
                  ? "fill-green-600 text-green-600"
                  : "fill-gray-400 text-gray-400"
              )}
            />
            <span className="capitalize">{status}</span>
          </div> */}
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

      {type !== "webhook" && (
        <Handle
          type="target"
          position={Position.Top}
          className="w-16 !bg-gray-500"
          style={handleStyle}
        />
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="w-16 !bg-gray-500"
        style={handleStyle}
      />
    </Card>
  )
})
