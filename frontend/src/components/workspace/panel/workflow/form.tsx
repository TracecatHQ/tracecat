"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { SaveIcon, Settings2Icon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Workflow } from "@/types/schemas"
import { useSaveWorkflow } from "@/lib/hooks"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
  const form = useForm<TWorkflowForm>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
      title: workflowTitle || "",
      description: workflowDescription || "",
    },
  })
  const { mutateAsync: saveAsync } = useSaveWorkflow(workflowId)

  const onSubmit = async (values: TWorkflowForm) => {
    console.log("Saving changes...")
    await saveAsync(values)
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <div className="flex flex-1 justify-end gap-2 p-4">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="submit" size="icon">
                <SaveIcon className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Save Changes</TooltipContent>
          </Tooltip>
          <WorkflowSettings workflow={workflow} />
        </div>
        <Separator />
        <Accordion type="single" collapsible>
          <AccordionItem value="workflow-settings">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <div className="flex items-center">
                <Settings2Icon className="mr-3 size-4" />
                <span>Workflow</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 space-y-2 px-4">
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
      </form>
    </Form>
  )
}
