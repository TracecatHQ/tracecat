"use client"

import { useEffect, useState } from "react"
import * as React from "react"
import { useWorkflowMetadata } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"

import { PlayIcon, ZapIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"
import { z } from "zod"

import { Action, Workflow } from "@/types/schemas"
import { stringToJSONSchema } from "@/types/validators"
import { triggerWorkflow } from "@/lib/flow"
import { getActionKey } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"

const workflowControlsFormSchema = z
  .object({
    payload: stringToJSONSchema, // json
    actionKey: z.string().optional(),
  })
  .refine(
    (data) => {
      return data.actionKey !== undefined
    },
    {
      message: "Please select an action.",
      path: ["actionKey"], // Specify the path of the field being validated for targeted error messages.
    }
  )
type TWorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

export function WorkflowControlsForm({
  workflow,
}: {
  workflow: Workflow
}): React.JSX.Element {
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      payload: "",
      actionKey: undefined,
    },
  })
  const [selectedAction, setSelectedAction] = useState<Action | null>(null)

  const onSubmit = async (values: TWorkflowControlsForm) => {
    // Make the API call to start the workflow
    if (!values.actionKey) {
      console.error("No action key provided")
      toast({
        title: "No action key provided",
        description: "Please select an action to start the workflow.",
      })
      return
    }

    try {
      await triggerWorkflow(workflow.id, values.actionKey, values.payload)
      toast({
        title: "Workflow started",
        description: "The workflow has been started successfully.",
      })
    } catch (error) {
      console.error("Error starting workflow", error)
      toast({
        title: "Error starting workflow",
        description: "Please check the run logs for more information",
      })
    }
  }

  useEffect(() => {
    if (selectedAction) {
      console.log("Selected action", selectedAction)
      form.setValue("actionKey", getActionKey(selectedAction))
    }
  }, [selectedAction])

  return (
    <Accordion type="single" defaultValue="workflow-triggers" collapsible>
      <AccordionItem value="workflow-triggers">
        <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
          <div className="flex items-center">
            <ZapIcon className="mr-3 size-4" />
            <span>Run Workflow</span>
          </div>
        </AccordionTrigger>
        <AccordionContent>
          <div className="px-4 my-4">
            <Form {...form}>
              <form onSubmit={form.handleSubmit(onSubmit)}>
                <FormField
                  control={form.control}
                  name="payload"
                  render={({ field }) => (
                    <FormItem>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <FormLabel className="text-xs decoration-dotted underline underline-offset-2">Trigger Parameters</FormLabel>
                        </TooltipTrigger>
                        <TooltipContent>JSON input to send to the selected webhook and trigger the workflow.</TooltipContent>
                      </Tooltip>
                      <FormControl>
                        <Textarea
                          className="text-xs"
                          placeholder='{"webhookParam1": "value1"}'
                          {...field}
                        />
                      </FormControl>
                      <div className="flex w-full items-center space-x-2 pt-2">
                        <EntrypointSelector setSelectedAction={setSelectedAction} />
                        <Button
                          type="submit"
                          className="flex items-center text-xs"
                        >
                          <PlayIcon className="mr-2 size-3" />
                          <span>Run</span>
                        </Button>
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </form>
            </Form>
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  )
}

export default function EntrypointSelector({
  setSelectedAction,
}: {
  setSelectedAction: React.Dispatch<React.SetStateAction<Action | null>>
}) {
  const { workflow } = useWorkflowMetadata()
  const [actions, setActions] = useState<Action[]>([])

  useEffect(() => {
    if (workflow?.actions) {
      setActions(
        Object.values(workflow.actions).filter(
          (action) => action.type === "webhook"
        )
      )
    }
  }, [workflow?.actions])

  return (
    <Select>
      <SelectTrigger>
        <SelectValue
          placeholder="Select webhook"
          className="text-xs text-muted-foreground"
        />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel className="text-xs">Webhooks</SelectLabel>
          {actions.map((action) => (
            <SelectItem
              key={action.id}
              value={action.id}
              className="text-xs"
              onSelect={() => {
                setSelectedAction(action)
              }}
            >
              {action.title}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
