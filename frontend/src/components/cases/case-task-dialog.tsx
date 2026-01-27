"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Trash2 } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseTaskRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"
import {
  useCreateCaseTask,
  useUpdateCaseTask,
  useWorkflowManager,
} from "@/lib/hooks"
import { DeleteCaseTaskDialog } from "./delete-case-task-dialog"

const taskSchema = z.object({
  title: z.string().min(1, "Title is required"),
  description: z.string(),
  status: z.enum(["todo", "in_progress", "completed", "blocked"]),
  priority: z.enum(["unknown", "low", "medium", "high", "critical", "other"]),
  assignee_id: z.string().nullable(),
  workflow_id: z.string().nullable(),
})

type TaskSchema = z.infer<typeof taskSchema>

interface CaseTaskDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  task: CaseTaskRead | null // null = create mode, populated = edit mode
  caseId: string
  workspaceId: string
  onCreateSuccess?: () => void
}

const UNASSIGNED = "unassigned"

export function CaseTaskDialog({
  open,
  onOpenChange,
  task,
  caseId,
  workspaceId,
  onCreateSuccess,
}: CaseTaskDialogProps) {
  const { members } = useWorkspaceMembers(workspaceId)
  const { workflows } = useWorkflowManager()
  const isEditMode = !!task
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const { createTask, createTaskIsPending } = useCreateCaseTask({
    caseId,
    workspaceId,
  })
  const { updateTask, updateTaskIsPending } = useUpdateCaseTask({
    caseId,
    workspaceId,
    taskId: task?.id || "",
  })

  const form = useForm<TaskSchema>({
    resolver: zodResolver(taskSchema),
    defaultValues: {
      title: "",
      description: "",
      status: "todo",
      priority: "unknown",
      assignee_id: null,
      workflow_id: null,
    },
  })

  useEffect(() => {
    if (open) {
      if (task) {
        form.reset({
          title: task.title,
          description: task.description || "",
          priority: task.priority as TaskSchema["priority"],
          status: task.status as TaskSchema["status"],
          assignee_id: task.assignee?.id || null,
          workflow_id: task.workflow_id || null,
        })
      } else {
        form.reset({
          title: "",
          description: "",
          status: "todo",
          priority: "unknown",
          assignee_id: null,
          workflow_id: null,
        })
      }
    }
  }, [open, task, form])

  const onSubmit = (data: TaskSchema) => {
    if (isEditMode) {
      updateTask(data, {
        onSuccess: () => {
          onOpenChange(false)
        },
      })
    } else {
      createTask(data, {
        onSuccess: () => {
          onOpenChange(false)
          form.reset()
          onCreateSuccess?.()
        },
      })
    }
  }

  const isPending = isEditMode ? updateTaskIsPending : createTaskIsPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEditMode ? "Edit task" : "Create task"}</DialogTitle>
          <DialogDescription>
            {isEditMode
              ? "Update the task details below."
              : "Add a new task to track follow-up actions for this case."}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Title</FormLabel>
                  <FormControl>
                    <Input
                      className="text-xs"
                      placeholder="Short description of the task..."
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea
                      className="text-xs"
                      placeholder="Detailed description of the task..."
                      rows={3}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="status"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Status</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="todo">To do</SelectItem>
                          <SelectItem value="in_progress">
                            In Progress
                          </SelectItem>
                          <SelectItem value="completed">Completed</SelectItem>
                          <SelectItem value="blocked">Blocked</SelectItem>
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="priority"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Priority</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select priority" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="unknown">Unknown</SelectItem>
                          <SelectItem value="low">Low</SelectItem>
                          <SelectItem value="medium">Medium</SelectItem>
                          <SelectItem value="high">High</SelectItem>
                          <SelectItem value="critical">Critical</SelectItem>
                          <SelectItem value="other">Other</SelectItem>
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="assignee_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Assignee</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value || UNASSIGNED}
                        onValueChange={(value) =>
                          field.onChange(value === UNASSIGNED ? null : value)
                        }
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select assignee" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={UNASSIGNED}>Unassigned</SelectItem>
                          {members?.map((member) => {
                            const displayName = getDisplayName({
                              email: member.email,
                              first_name: member.first_name,
                              last_name: member.last_name,
                            })
                            return (
                              <SelectItem
                                key={member.user_id}
                                value={member.user_id}
                              >
                                {displayName}
                              </SelectItem>
                            )
                          })}
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="workflow_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Workflow</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value || "__none__"}
                        onValueChange={(value) =>
                          field.onChange(value === "__none__" ? null : value)
                        }
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select workflow (optional)" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">None</SelectItem>
                          {workflows?.map((workflow) => (
                            <SelectItem key={workflow.id} value={workflow.id}>
                              <span>
                                {workflow.title}
                                {workflow.alias && (
                                  <span className="italic text-muted-foreground ml-1">
                                    ({workflow.alias})
                                  </span>
                                )}
                              </span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter className="flex flex-row items-center justify-between">
              <div className="flex-1">
                {isEditMode && (
                  <Button
                    type="button"
                    variant="ghost"
                    className="text-rose-600 hover:text-rose-700 hover:bg-rose-50"
                    onClick={() => setDeleteDialogOpen(true)}
                    disabled={isPending}
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    Delete
                  </Button>
                )}
              </div>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => onOpenChange(false)}
                  disabled={isPending}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={isPending}>
                  {isPending
                    ? isEditMode
                      ? "Saving..."
                      : "Creating..."
                    : isEditMode
                      ? "Save changes"
                      : "Create task"}
                </Button>
              </div>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>

      <DeleteCaseTaskDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        task={task}
        caseId={caseId}
        workspaceId={workspaceId}
        onDeleteSuccess={() => onOpenChange(false)}
      />
    </Dialog>
  )
}
