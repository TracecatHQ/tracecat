import {
  AlarmClockOffIcon,
  CircleCheck,
  CircleX,
  Ellipsis,
  MessagesSquare,
} from "lucide-react"
import type { InteractionStatus } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { WorkflowExecutionReadCompact } from "@/lib/event-history"
import { cn, undoSlugify } from "@/lib/utils"

export function WorkflowInteractions({
  execution,
}: {
  execution: WorkflowExecutionReadCompact
}) {
  const interactions = execution.interactions || []
  return (
    <ScrollArea className="p-4 pt-0">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
        <MessagesSquare className="size-3" />
        <span>Interactions</span>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="h-8 text-xs">Status</TableHead>
              <TableHead className="h-8 text-xs">Action</TableHead>
              <TableHead className="h-8 text-xs">Type</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {interactions && Object.keys(interactions).length > 0 ? (
              Object.entries(interactions).map(
                ([id, { action_ref, type, status }]) => (
                  <TableRow key={id}>
                    <TableCell className="p-0 text-xs font-medium">
                      <div className="flex size-full items-center justify-start pl-4">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            {getInteractionStatusIcon(status, "size-4")}
                          </TooltipTrigger>
                          <TooltipContent
                            side="top"
                            className="flex items-center gap-4  shadow-lg"
                          >
                            <span>{undoSlugify(status.toLowerCase())}</span>
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-28 text-xs text-foreground/70">
                      {action_ref}
                    </TableCell>
                    <TableCell className="max-w-28 text-xs text-foreground/70">
                      {type}
                    </TableCell>
                  </TableRow>
                )
              )
            ) : (
              <TableRow className="justify-center text-xs text-muted-foreground">
                <TableCell
                  className="h-8 items-center justify-center bg-muted-foreground/5 text-center"
                  colSpan={3}
                >
                  <div className="flex items-center justify-center gap-2">
                    <span>No interactions.</span>
                  </div>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </ScrollArea>
  )
}

export function getInteractionStatusIcon(
  status: InteractionStatus,
  className?: string
) {
  switch (status) {
    case "idle":
      return (
        <Ellipsis
          className={cn(
            "size-3 animate-[wave_1.5s_ease-in-out_infinite]",
            className
          )}
        />
      )
    case "pending":
      return <Spinner className={cn("size-3", className)} />
    case "error":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    case "timed_out":
      return (
        <AlarmClockOffIcon
          className={cn("!size-3 stroke-rose-500", className)}
          strokeWidth={2.5}
        />
      )
    case "completed":
      return (
        <CircleCheck
          className={cn(
            "border-none border-emerald-500 fill-emerald-500 stroke-white",
            className
          )}
        />
      )
    default:
      throw new Error("Invalid status")
  }
}
