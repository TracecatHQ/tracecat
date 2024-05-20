"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { SaveIcon, Settings2Icon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Workflow } from "@/types/schemas"
import { updateWorkflow } from "@/lib/flow"
import { Button } from "@/components/ui/button"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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

type TWorkflowForm = z.infer<typeof workflowFormSchema>

interface WorkflowFormProps {
  workflow: Workflow
}

export function WorkflowForm({
  workflow,
}: WorkflowFormProps): React.JSX.Element {
  const {
    id: workflowId,
    title: workflowTitle,
    description: workflowDescription,
  } = workflow
  const queryClient = useQueryClient()
  const form = useForm<TWorkflowForm>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
      title: workflowTitle || "",
      description: workflowDescription || "",
    },
  })

  function useUpdateWorkflow(workflowId: string) {
    const mutation = useMutation({
      mutationFn: (values: TWorkflowForm) => updateWorkflow(workflowId, values),
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
  function onSubmit(values: TWorkflowForm) {
    mutate(values)
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <div className="flex flex-1 justify-end space-x-2 px-4 mb-4">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="submit" size="icon">
                <SaveIcon className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Save</TooltipContent>
          </Tooltip>
          <WorkflowSettings workflow={workflow} />
        </div>
        <Separator />
        <Accordion type="single" defaultValue="workflow-settings" collapsible>
          <AccordionItem value="workflow-settings">
            <AccordionTrigger className="px-4 text-xs">
              <div className="flex items-center">
                <Settings2Icon className="mr-2 size-4" />
                <span>Workflow</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="space-y-2 px-4 my-4">
                <FormField
                  control={form.control}
                  name="title"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Name</FormLabel>
                      <FormControl>
                        <Input
                          className="text-xs"
                          placeholder="Name your workflow..."
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
                          className="text-xs"
                          placeholder="Describe your workflow..."
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </form >
    </Form >
  )
}
