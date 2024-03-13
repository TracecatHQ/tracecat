import React from "react"
import { ActionType } from "@/types"
import {
  BellDotIcon,
  Blend,
  BookText,
  CheckSquare,
  ChevronDownIcon,
  CircleIcon,
  Container,
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
import { Handle, NodeProps, Position, type Node } from "reactflow"

import { cn } from "@/lib/utils"
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

export type ActionNodeType = Node<ActionNodeData>
export interface ActionNodeData {
  type: ActionType
  title: string
  status: "online" | "offline"
  isConfigured: boolean
  numberOfEvents: number
  // Generic metadata
}

const tileIconMapping: Partial<Record<ActionType, LucideIcon>> = {
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

const handleStyle = { width: 8, height: 8 }

export default React.memo(function ActionNode({
  data: { type, title, status, isConfigured, numberOfEvents },
}: NodeProps<ActionNodeData>) {
  const avatarImageAlt = `${type}-${title}`
  const tileIcon = tileIconMapping[type] ?? Sparkles
  const isConfiguredMessage = isConfigured ? "ready" : "missing inputs"

  return (
    <Card>
      <CardHeader className="grid p-4 pl-5 pr-5">
        <div className="flex w-full items-center space-x-4">
          <Avatar>
            <AvatarImage src="" alt={avatarImageAlt} />
            <AvatarFallback>
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
              <DropdownMenuContent className="p-0" align="end">
                <DropdownMenuItem>
                  <ScanSearchIcon className="mr-2 h-4 w-4" />
                  <span className="text-xs">Search events</span>
                </DropdownMenuItem>
                <DropdownMenuItem>
                  <EyeIcon className="mr-2 h-4 w-4" />
                  <span className="text-xs">View logs</span>
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
                status === "online"
                  ? "fill-green-600 text-green-600"
                  : "fill-gray-400 text-gray-400"
              )}
            />
            <span>{status}</span>
          </div>
          <div className="flex items-center">
            <CircleIcon
              className={cn(
                "mr-1 h-3 w-3",
                isConfigured
                  ? "fill-green-600 text-green-600"
                  : "fill-gray-400 text-gray-400"
              )}
            />
            <span>{isConfiguredMessage}</span>
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
