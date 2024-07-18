"use client"

import { useCallback } from "react"
import * as React from "react"
import { ApiError, workflowExecutionsCreateWorkflowExecution } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { PlayIcon, ZapIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import JsonView from "react18-json-view"
import { z } from "zod"

import { Workflow } from "@/types/schemas"
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"

const workflowControlsFormSchema = z.object({
  payload: z.record(z.string(), z.unknown()).optional(),
})
type TWorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

export function WorkflowControls({
  workflow,
}: {
  workflow: Workflow
}): React.JSX.Element {
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {},
  })

  const handleSubmit = useCallback(async () => {
    // Make the API call to start the workflow
    const values = form.getValues()
    try {
      const response = await workflowExecutionsCreateWorkflowExecution({
        requestBody: {
          workflow_id: workflow.id,
          inputs: values.payload,
        },
      })
      console.log("Workflow started", response)
      toast({
        title: `Workflow run started`,
        description: `${response.wf_exec_id} ${response.message}`,
      })
    } catch (error) {
      if (error instanceof ApiError) {
        console.error("Error details", error.body)
        toast({
          title: "Error starting workflow",
          description: error.message,
          variant: "destructive",
        })
      } else {
        console.error("Unexpected error starting workflow", error)
        toast({
          title: "Unexpected error starting workflow",
          description: "Please check the run logs for more information",
          variant: "destructive",
        })
      }
    }
  }, [form])

  return (
    <Accordion type="single" collapsible defaultValue="workflow-trigger">
      <AccordionItem value="workflow-trigger">
        <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
          <div className="flex items-center">
            <ZapIcon className="mr-3 size-4" />
            <span>Run Workflow</span>
          </div>
        </AccordionTrigger>
        <AccordionContent>
          <div className="my-4 px-4">
            <Form {...form}>
              <form>
                <FormField
                  control={form.control}
                  name="payload"
                  render={({ field }) => (
                    <FormItem>
                      <div className="flex items-center">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <FormLabel className="mr-auto text-xs underline decoration-dotted underline-offset-2">
                              Trigger Parameters
                            </FormLabel>
                          </TooltipTrigger>

                          <TooltipContent>
                            JSON input to send to the selected webhook and
                            trigger the workflow.
                          </TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={handleSubmit}
                            className="group flex h-7 items-center px-3 py-0 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white"
                          >
                            <PlayIcon className="mr-2 size-3 fill-emerald-500 stroke-emerald-500 group-hover:fill-white group-hover:stroke-white" />
                            <span>Run</span>
                          </Button>
                        </Tooltip>
                      </div>
                      <FormControl>
                        <div className="w-full rounded-md border p-4">
                          {/* The json contains the view into the data */}
                          <JsonView
                            displaySize
                            editable
                            enableClipboard
                            src={field.value}
                            className="text-sm"
                            theme="atom"
                            onChange={(value) => {
                              console.log("JsonView onChange", value.src)
                              field.onChange(value.src)
                            }}
                          />
                        </div>
                      </FormControl>

                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="flex w-full items-center space-x-2 pt-2"></div>
              </form>
            </Form>
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  )
}
