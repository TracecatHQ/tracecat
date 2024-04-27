"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CircleIcon, Save } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Workflow } from "@/types/schemas"
import { updateWorkflow } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { WorkflowSettings } from "@/components/workspace/panel/workflow/settings"

const workflowFormSchema = z.object({
  title: z.string(),
  description: z.string(),
})

type WorkflowForm = z.infer<typeof workflowFormSchema>

interface WorkflowFormProps {
  workflow: Workflow
  isOnline: boolean
}

export function WorkflowForm({
  workflow,
  isOnline,
}: WorkflowFormProps): React.JSX.Element {
  const {
    id: workflowId,
    title: workflowTitle,
    description: workflowDescription,
  } = workflow
  const queryClient = useQueryClient()
  const form = useForm<WorkflowForm>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
      title: workflowTitle || "",
      description: workflowDescription || "",
    },
  })

  function useUpdateWorkflow(workflowId: string) {
    const mutation = useMutation({
      mutationFn: (values: WorkflowForm) => updateWorkflow(workflowId, values),
      onSuccess: (data) => {
        console.log("Workflow update successful", data)
        queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
        toast({
          title: "Saved workflow",
          description: "Workflow updated successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to update workflow:", error)
        toast({
          title: "Error updating workflow",
          description: "Could not update workflow. Please try again.",
        })
      },
    })

    return mutation
  }

  const { mutate } = useUpdateWorkflow(workflowId)
  function onSubmit(values: WorkflowForm) {
    mutate(values)
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-4">
          <div className="space-y-3">
            <div className="flex justify-between">
              <h4 className="text-sm font-medium">Workflow Status</h4>
              <WorkflowSettings workflow={workflow} />
            </div>
            <div className="flex justify-between">
              <Badge
                variant="outline"
                className={cn(
                  "px-4 py-1 capitalize",
                  isOnline ? "bg-green-600/10" : "bg-gray-100"
                )}
              >
                <CircleIcon
                  className={cn(
                    "mr-2 h-3 w-3",
                    isOnline
                      ? "fill-green-600 text-green-600"
                      : "fill-gray-400 text-gray-400"
                  )}
                />
                <span
                  className={cn(isOnline ? "text-green-600" : "text-gray-600")}
                >
                  {isOnline ? "online" : "offline"}
                </span>
              </Badge>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button type="submit" size="icon">
                    <Save className="h-4 w-4" />
                    <span className="sr-only">Save</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Save</TooltipContent>
              </Tooltip>
            </div>
          </div>
          <Separator />
          <div className="space-y-4">
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-xs">Name</FormLabel>
                  <FormControl>
                    <Input
                      className="text-xs"
                      placeholder="Add workflow name..."
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
                  <FormLabel className="text-xs">Description</FormLabel>
                  <FormControl>
                    <Textarea
                      className="min-h-[100px] text-xs"
                      placeholder="Describe your workflow..."
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
        </div>
      </form>
    </Form>
  )
}
