import React from "react"
import {
  BellDotIcon,
  Blend,
  ChevronsDownIcon,
  CircleIcon,
  EyeIcon,
  Globe,
  LucideIcon,
  Mail,
  ScanSearchIcon,
  Send,
  ShieldAlert,
  Sparkles,
  Split,
  Webhook,
} from "lucide-react"
import { Handle, NodeProps, Position } from "reactflow"

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

export interface ActionNodeData {
  type: string
  title: string
  status: "online" | "offline"
  isConfigured: boolean
  numberOfEvents: number
  // Generic metadata
}

const tileIconMapping: { [key: string]: LucideIcon } = {
  Webhook: Webhook,
  "HTTP Request": Globe,
  "Data Transform": Blend,
  "If Condition": Split,
  "Open Case": ShieldAlert,
  "Receive Email": Mail,
  "Send Email": Send,
  "AI Actions": Sparkles,
}
const handleStyle = { width: 8, height: 8 }

export default React.memo(function ActionNode({
  data: { type, title, status, isConfigured, numberOfEvents },
}: NodeProps<ActionNodeData>) {
  const avatarImageAlt = `${type}-${title}`
  const tileIcon = tileIconMapping[type]
  const isConfiguredMessage = isConfigured ? "ready" : "missing inputs"

  return (
    <Card>
      <CardHeader className="grid p-4 pl-5 pr-5">
        <div className="flex items-center justify-between space-x-16">
          <div className="flex items-center space-x-4">
            <Avatar>
              <AvatarImage src="" alt={avatarImageAlt} />
              <AvatarFallback>
                {React.createElement(tileIcon, { className: "h-5 w-5" })}
              </AvatarFallback>
            </Avatar>
            <div>
              <CardTitle className="text-sm font-medium leading-none">
                {title}
              </CardTitle>
              <CardDescription className="mt-1 text-sm text-muted-foreground">
                {type}
              </CardDescription>
            </div>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" className="ml-auto">
                <ChevronsDownIcon className="h-4 w-4 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="p-0" align="end">
              <DropdownMenuItem>
                <ScanSearchIcon className="mr-2 h-4 w-4" />
                <span>Search events</span>
              </DropdownMenuItem>
              <DropdownMenuItem>
                <EyeIcon className="mr-2 h-4 w-4" />
                <span>View logs</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </CardHeader>

      <CardContent className="pb-4 pl-5 pr-5 pt-0">
        <div className="flex space-x-4 text-xs text-muted-foreground">
          <div className="flex items-center">
            {status === "online" && (
              <CircleIcon className="mr-1 h-3 w-3 fill-green-600 text-green-600" />
            )}
            {status === "offline" && (
              <CircleIcon className="mr-1 h-3 w-3 fill-gray-400 text-gray-400" />
            )}
            <span>{status}</span>
          </div>
          <div className="flex items-center">
            {isConfigured && (
              <CircleIcon className="mr-1 h-3 w-3 fill-green-600 text-green-600" />
            )}
            {!isConfigured && (
              <CircleIcon className="mr-1 h-3 w-3 fill-gray-400 text-gray-400" />
            )}
            <span>{isConfiguredMessage}</span>
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
