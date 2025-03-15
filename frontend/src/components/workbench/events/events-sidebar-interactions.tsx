import { InteractionStatus, WorkflowExecutionReadCompact } from "@/client"
import { CircleCheck, Ellipsis, Loader2, MessagesSquare } from "lucide-react"

import { cn, undoSlugify } from "@/lib/utils"
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

export function WorkflowInteractions({
  execution,
}: {
  execution: WorkflowExecutionReadCompact
}) {
  const states = execution.interaction_states
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
            {states && Object.keys(states).length > 0 ? (
              Object.entries(states).map(
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
      return (
        <Loader2
          className={cn("size-3 animate-spin stroke-blue-500/50", className)}
          strokeWidth={3}
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
