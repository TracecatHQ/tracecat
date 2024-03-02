import React from "react"
import { Handle, NodeProps, Position } from "reactflow"

import {
  ChevronsDownIcon,
  CircleIcon,
  GanttChartIcon,
  PlayIcon,
  TestTubeIcon,
} from "lucide-react"
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
  title: string
  name: string
  status: "online" | "error" | "offline"
  numberOfEvents: number
  // Generic metadata
}

const handleStyle = { width: 8, height: 8 }

export default React.memo(function ActionNode({
  data: { title, name, status, numberOfEvents },
}: NodeProps<ActionNodeData>) {
  return (
    <Card>
      <CardHeader className="grid grid-cols-[1fr_110px] items-start gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle>{title}</CardTitle>
          <CardDescription>{name}</CardDescription>
        </div>
        <div className="flex items-center space-x-1 rounded-md bg-secondary text-secondary-foreground">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="secondary" className="px-2 shadow-none">
                <ChevronsDownIcon className="h-4 w-4 text-secondary-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              alignOffset={-5}
              className="w-[200px]"
              forceMount
            >
              <DropdownMenuItem>
                <PlayIcon className="mr-2 h-4 w-4" /> Run
              </DropdownMenuItem>
              <DropdownMenuItem>
                <TestTubeIcon className="mr-2 h-4 w-4" /> Test action
              </DropdownMenuItem>
              <DropdownMenuItem>
                <GanttChartIcon className="mr-2 h-4 w-4" /> View events
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex space-x-4 text-sm text-muted-foreground">
          <div className="flex items-center">
            <CircleIcon className="mr-1 h-3 w-3 fill-sky-400 text-sky-400" /> {status}
          </div>
          <div className="flex items-center">
            <GanttChartIcon className="mr-1 h-3 w-3" /> {numberOfEvents} events
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
