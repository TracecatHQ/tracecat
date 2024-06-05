"use client"

import { useCallback, useEffect, useState } from "react"
import * as React from "react"
import { useWorkflow } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"
import { PlayIcon, ZapIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Action, Workflow } from "@/types/schemas"
import { stringToJSONSchema } from "@/types/validators"
import { getActionKey } from "@/lib/utils"
import { triggerWorkflow } from "@/lib/workflow"
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
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
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

  const handleSubmit = useCallback(async () => {
    // Make the API call to start the workflow
    const values = {
      ...form.getValues(),
      actionKey: selectedAction ? getActionKey(selectedAction) : undefined,
    }
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
  }, [selectedAction, form, setSelectedAction])

  return (
    <Accordion type="single" collapsible>
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
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <FormLabel className="text-xs underline decoration-dotted underline-offset-2">
                            Trigger Parameters
                          </FormLabel>
                        </TooltipTrigger>
                        <TooltipContent>
                          JSON input to send to the selected webhook and trigger
                          the workflow.
                        </TooltipContent>
                      </Tooltip>
                      <FormControl>
                        <Textarea
                          className="text-xs"
                          placeholder='{"webhookParam1": "value1"}'
                          {...field}
                        />
                      </FormControl>

                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="flex w-full items-center space-x-2 pt-2">
                  <EntrypointSelector setSelectedAction={setSelectedAction} />
                  <Button
                    type="button"
                    onClick={handleSubmit}
                    className="flex items-center text-xs"
                  >
                    <PlayIcon className="mr-2 size-3" />
                    <span>Run</span>
                  </Button>
                </div>
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
  const { workflow } = useWorkflow()
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
