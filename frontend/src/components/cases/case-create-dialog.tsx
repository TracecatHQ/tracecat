"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useRouter } from "next/navigation"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CasePriority, CaseRead, CaseSeverity, CaseStatus } from "@/client"
import { ApiError } from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
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
import { useCreateCase } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const createCaseSchema = z.object({
  summary: z
    .string()
    .min(1, "Summary is required")
    .max(200, "Summary cannot exceed 200 characters"),
  description: z.string().min(1, "Description is required"),
  status: z.string() as z.ZodType<CaseStatus>,
  priority: z.string() as z.ZodType<CasePriority>,
  severity: z.string() as z.ZodType<CaseSeverity>,
  assignee_id: z.string().optional(),
})

type CreateCaseSchema = z.infer<typeof createCaseSchema>

export function CreateCaseDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { createCase, createCaseIsPending } = useCreateCase(workspaceId)

  const form = useForm<CreateCaseSchema>({
    resolver: zodResolver(createCaseSchema),
    defaultValues: {
      summary: "",
      description: "",
      status: "new",
      priority: "medium",
      severity: "medium",
    },
    mode: "onSubmit",
  })

  const onSubmit = async (data: CreateCaseSchema) => {
    try {
      const response = await createCase({
        summary: data.summary,
        description: data.description,
        status: data.status,
        priority: data.priority,
        severity: data.severity,
        assignee_id: data.assignee_id || null,
      })

      onOpenChange(false)
      form.reset()

      // Navigate to the newly created case
      // Note: The response type is unknown, so we need to cast it
      const caseResponse = response as CaseRead | undefined
      if (caseResponse?.id) {
        router.push(`/workspaces/${workspaceId}/cases/${caseResponse.id}`)
      }
    } catch (error) {
      if (error instanceof ApiError) {
        form.setError("root", {
          type: "manual",
          message: error.message,
        })
      } else {
        form.setError("root", {
          type: "manual",
          message:
            error instanceof Error ? error.message : "Failed to create case",
        })
        console.error("Error creating case:", error)
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[625px]">
        <DialogHeader>
          <DialogTitle>Create new case</DialogTitle>
          <DialogDescription>
            Create a new case to track and manage incidents and issues.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="summary"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Summary</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Brief description of the case..."
                      {...field}
                      value={field.value ?? ""}
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
                      placeholder="Detailed description of the case..."
                      className="min-h-[100px] text-xs"
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-3 gap-4">
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
                          <SelectValue placeholder="Select status" />
                        </SelectTrigger>
                        <SelectContent>
                          {Object.values(STATUSES).map((status) => (
                            <SelectItem key={status.value} value={status.value}>
                              <div className="flex items-center gap-2">
                                <status.icon className="h-3 w-3" />
                                {status.label}
                              </div>
                            </SelectItem>
                          ))}
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
                          {Object.values(PRIORITIES).map((priority) => (
                            <SelectItem
                              key={priority.value}
                              value={priority.value}
                            >
                              <div className="flex items-center gap-2">
                                <priority.icon className="h-3 w-3" />
                                {priority.label}
                              </div>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="severity"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Severity</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select severity" />
                        </SelectTrigger>
                        <SelectContent>
                          {Object.values(SEVERITIES).map((severity) => (
                            <SelectItem
                              key={severity.value}
                              value={severity.value}
                            >
                              <div className="flex items-center gap-2">
                                <severity.icon className="h-3 w-3" />
                                {severity.label}
                              </div>
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

            {form.formState.errors.root && (
              <div className="text-sm text-red-500">
                {form.formState.errors.root.message}
              </div>
            )}

            <DialogFooter>
              <Button type="submit" disabled={createCaseIsPending}>
                Create case
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
