import React from "react"
import { Schedule, WebhookResponse } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import {
  CalendarCheck,
  ChevronDownIcon,
  CircleCheckBigIcon,
  EyeIcon,
  LayoutListIcon,
  ScanSearchIcon,
  TimerOffIcon,
  UnplugIcon,
  WebhookIcon,
} from "lucide-react"
import { Node, NodeProps } from "reactflow"

import { useSchedules } from "@/lib/hooks"
import { durationToHumanReadable } from "@/lib/time"
import { cn } from "@/lib/utils"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getIcon } from "@/components/icons"
import { TriggerSourceHandle } from "@/components/workbench/canvas/custom-handle"

export interface TriggerNodeData {
  type: "trigger"
  title: string
  status: "online" | "offline"
  isConfigured: boolean
  entrypointId?: string
  webhook: WebhookResponse
  schedules: Schedule[]
}
export type TriggerNodeType = Node<TriggerNodeData>
export const TriggerTypename = "trigger" as const

export default React.memo(function TriggerNode({
  data: { title, isConfigured, type },
  selected,
}: NodeProps<TriggerNodeData>) {
  const { workflow } = useWorkflow()

  if (!workflow) {
    return null
  }

  return (
    <Card className={cn("min-w-72", selected && "shadow-xl drop-shadow-xl")}>
      <CardHeader className="p-4">
        <div className="flex w-full items-center space-x-4">
          {getIcon(type, {
            className: "size-10 p-2",
          })}

          <div className="flex w-full flex-1 justify-between space-x-12">
            <div className="flex flex-col">
              <CardTitle className="flex w-full items-center justify-between text-xs font-medium leading-none">
                <div className="flex w-full">{title}</div>
              </CardTitle>
              <CardDescription className="mt-2 text-xs text-muted-foreground">
                Workflow triggers
              </CardDescription>
            </div>
          </div>
        </div>
      </CardHeader>
      <Separator />
      <div className="p-4">
        <div className="space-y-4">
          {/* Webhook status */}
          <div
            className={cn(
              "flex h-8 items-center justify-center gap-1 rounded-lg border text-xs text-muted-foreground",
              workflow?.webhook.status === "offline"
                ? "bg-muted-foreground/5 text-muted-foreground/50"
                : "bg-background text-emerald-500"
            )}
          >
            <WebhookIcon className="size-3" />
            <span>Webhook</span>
            <span
              className={cn(
                "ml-2 inline-block size-2 rounded-full ",
                workflow.webhook.status === "online"
                  ? "bg-emerald-500"
                  : "bg-gray-300"
              )}
            />
          </div>
          {/* Schedule table */}
          <div className="rounded-lg border">
            <TriggerNodeSchedulesTable workflowId={workflow.id} />
          </div>
        </div>
      </div>
      <Separator />
      <TriggerSourceHandle />
    </Card>
  )
})

function TriggerNodeSchedulesTable({ workflowId }: { workflowId: string }) {
  const { schedules, schedulesIsLoading, schedulesError } =
    useSchedules(workflowId)

  if (schedulesIsLoading) {
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="h-8 text-center text-xs" colSpan={2}>
              <div className="flex items-center justify-center gap-1">
                <Skeleton className="size-3" />
                <Skeleton className="h-3 w-16" />
              </div>
            </TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          <TableRow className="items-center text-center text-xs">
            <TableCell>
              <div className="flex w-full items-center justify-center gap-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="size-2 rounded-full" />
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    )
  }
  if (schedulesError || !schedules) {
    return <UnplugIcon className="size-4 text-muted-foreground" />
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="h-8 text-center text-xs" colSpan={2}>
            <div className="flex items-center justify-center gap-1">
              <CalendarCheck className="size-3" />
              <span>Schedules</span>
            </div>
          </TableHead>
        </TableRow>
      </TableHeader>

      <TableBody>
        {schedules.length > 0 ? (
          schedules.map(({ status, every }, idx) => (
            <TableRow
              key={idx}
              className="items-center text-center text-xs text-muted-foreground"
            >
              <TableCell>
                <div className="flex w-full items-center justify-center">
                  <span
                    className={cn(
                      status === "offline" && "text-muted-foreground/80"
                    )}
                  >
                    Every {durationToHumanReadable(every)}
                  </span>
                  {status === "online" ? (
                    <span
                      className={cn(
                        "ml-2 inline-block size-2 rounded-full ",
                        status === "online" ? "bg-emerald-500" : "bg-gray-300"
                      )}
                    />
                  ) : (
                    <TimerOffIcon className="ml-2 size-3 text-muted-foreground" />
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))
        ) : (
          <TableRow className="justify-center text-xs text-muted-foreground">
            <TableCell
              className="h-8 bg-muted-foreground/5 text-center"
              colSpan={4}
            >
              No Schedules
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  )
}
