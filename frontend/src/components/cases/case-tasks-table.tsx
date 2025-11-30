"use client"

import { Circle, CircleCheck, CircleDashed, CircleDot } from "lucide-react"
import { useMemo, useState } from "react"
import type { CaseRead, CaseTaskRead, WorkflowReadMinimal } from "@/client"
import { CaseTaskDialog } from "@/components/cases/case-task-dialog"
import { TaskWorkflowTrigger } from "@/components/cases/task-workflow-trigger"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useWorkflowManager } from "@/lib/hooks"
import { capitalizeFirst } from "@/lib/utils"

interface CaseTasksTableProps {
  tasks: CaseTaskRead[]
  isLoading: boolean
  error: Error | null
  caseId: string
  workspaceId: string
  caseData: CaseRead
}

const PRIORITY_COLORS: Record<string, string> = {
  unknown: "bg-muted/50 text-muted-foreground",
  low: "bg-yellow-500/10 text-yellow-700 border-yellow-500/20",
  medium: "bg-orange-500/10 text-orange-700 border-orange-500/20",
  high: "bg-red-500/10 text-red-700 border-red-500/20",
  critical: "bg-fuchsia-500/10 text-fuchsia-700 border-fuchsia-500/20",
  other: "",
}

const STATUS_LABELS: Record<string, string> = {
  todo: "To do",
  in_progress: "In Progress",
  completed: "Completed",
  blocked: "Blocked",
}

const STATUS_ICONS: Record<
  string,
  React.ComponentType<{ className?: string }>
> = {
  todo: Circle,
  in_progress: CircleDot,
  completed: CircleCheck,
  blocked: CircleDashed,
}

const STATUS_ICON_COLORS: Record<string, string> = {
  todo: "text-slate-400",
  in_progress: "text-blue-500",
  completed: "text-green-500",
  blocked: "text-red-500",
}

export function CaseTasksTable({
  tasks,
  isLoading,
  error,
  caseId,
  workspaceId,
  caseData,
}: CaseTasksTableProps) {
  const { workflows } = useWorkflowManager()
  const [selectedTask, setSelectedTask] = useState<CaseTaskRead | null>(null)
  const [editDialogOpen, setEditDialogOpen] = useState(false)

  const workflowById = useMemo(() => {
    const map = new Map<string, WorkflowReadMinimal>()
    workflows?.forEach((workflow) => map.set(workflow.id, workflow))
    return map
  }, [workflows])

  const handleEditTask = (task: CaseTaskRead) => {
    setSelectedTask(task)
    setEditDialogOpen(true)
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[...Array(3)].map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
        Failed to load tasks
      </div>
    )
  }

  if (tasks.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
        No tasks
      </div>
    )
  }

  return (
    <>
      <div className="space-y-1">
        {tasks.map((task) => {
          const workflow = task.workflow_id
            ? workflowById.get(task.workflow_id)
            : null
          const StatusIcon = STATUS_ICONS[task.status] || Circle

          return (
            <div key={task.id} className="flex items-center gap-2">
              <Button
                type="button"
                variant="ghost"
                className="group relative w-full flex items-center gap-2.5 rounded-md border border-border/40 bg-background/60 px-3 py-2 backdrop-blur-sm transition-all hover:border-border hover:bg-muted/30 cursor-pointer flex-1 justify-start text-left"
                onClick={() => handleEditTask(task)}
                aria-label={`Edit task ${task.title}`}
              >
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="flex-shrink-0">
                        <StatusIcon
                          className={`size-4 ${STATUS_ICON_COLORS[task.status]}`}
                        />
                      </div>
                    </TooltipTrigger>
                    <TooltipContent side="left" className="text-xs">
                      {STATUS_LABELS[task.status]}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>

                <div className="flex-1 min-w-0 flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground truncate">
                    {task.title}
                  </span>
                  {task.description && (
                    <span className="text-xs text-muted-foreground/70 truncate hidden md:inline">
                      · {task.description}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-3 flex-shrink-0">
                  {task.priority && task.priority !== "unknown" && (
                    <Badge
                      variant="outline"
                      className={`text-xs h-5 px-1.5 ${PRIORITY_COLORS[task.priority]}`}
                    >
                      {capitalizeFirst(task.priority)}
                    </Badge>
                  )}
                  {task.assignee && (
                    <span className="text-xs text-muted-foreground hidden sm:inline">
                      {task.assignee.first_name ||
                        task.assignee.email.split("@")[0]}
                    </span>
                  )}
                </div>
              </Button>

              {workflow && (
                <TaskWorkflowTrigger
                  caseData={caseData}
                  workflow={workflow}
                  task={task}
                />
              )}
            </div>
          )
        })}
      </div>

      <CaseTaskDialog
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        task={selectedTask}
        caseId={caseId}
        workspaceId={workspaceId}
      />
    </>
  )
}
