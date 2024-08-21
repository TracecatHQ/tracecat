"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { useRouter } from "next/navigation"
import { ApiError, WorkflowResponse } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import {
  FileInputIcon,
  Info,
  SaveIcon,
  Settings2Icon,
  Undo2Icon,
} from "lucide-react"
import { Controller, useForm } from "react-hook-form"
import YAML from "yaml"
import { z } from "zod"

import { isEmptyObjectOrNullish } from "@/lib/utils"
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
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
import { CustomEditor } from "@/components/editor"

const workflowConfigFormSchema = z.object({
  title: z.string(),
  description: z.string(),
  static_inputs: z.string().transform((val, ctx) => {
    try {
      return YAML.parse(val) || {}
    } catch (error) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Invalid YAML format",
      })
      return z.NEVER
    }
  }),
  returns: z.string().transform((val, ctx) => {
    try {
      return YAML.parse(val) || null
    } catch (error) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Invalid YAML format",
      })
      return z.NEVER
    }
  }),
})

type WorkflowConfigForm = z.infer<typeof workflowConfigFormSchema>

export function WorkflowForm({
  workflow,
}: {
  workflow: WorkflowResponse
}): React.JSX.Element {
  const router = useRouter()
  const { updateWorkflow: update } = useWorkflow()
  const form = useForm<WorkflowConfigForm>({
    resolver: zodResolver(workflowConfigFormSchema),
    defaultValues: {
      title: workflow.title || "",
      description: workflow.description || "",
      static_inputs: isEmptyObjectOrNullish(workflow.static_inputs)
        ? ""
        : YAML.stringify(workflow.static_inputs),

      returns: !workflow.returns ? "" : YAML.stringify(workflow.returns),
    },
  })

  const onSubmit = async (values: WorkflowConfigForm) => {
    console.log("Saving changes...", values)
    try {
      await update(values)
      toast({
        title: "Saved changes",
        description: "Workflow updated successfully.",
      })
      // Refresh the workflow data
      router.refresh()
    } catch (error) {
      if (error instanceof ApiError) {
        toast({
          title: "Error saving changes",
          description: error.message,
          variant: "destructive",
        })
      }
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        {/* Save and Workflow Settings */}
        <div className="flex flex-1 justify-end gap-2 p-4">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="submit" size="icon">
                <SaveIcon className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Save Changes</TooltipContent>
          </Tooltip>
        </div>
        <Separator />
        {/* Workflow Settings */}
        <Accordion
          type="multiple"
          defaultValue={[
            "workflow-settings",
            "workflow-static-inputs",
            "workflow-returns",
          ]}
        >
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
                <div className="space-y-2">
                  <FormLabel className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span>Workflow ID</span>
                    <CopyButton
                      value={workflow.id}
                      toastMessage="Copied workflow ID to clipboard"
                    />
                  </FormLabel>
                  <div className="rounded-md border shadow-sm">
                    <Input
                      defaultValue={workflow.id}
                      className="rounded-md border-none text-xs shadow-none"
                      readOnly
                      disabled
                    />
                  </div>
                </div>
              </div>
            </AccordionContent>
          </AccordionItem>
          <AccordionItem value="workflow-static-inputs">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <div className="flex items-center">
                <Undo2Icon className="mr-3 size-4" />
                <span className="capitalize">
                  Output Schema
                </span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="flex flex-col space-y-4 px-4">
                <div className="flex items-center">
                  <HoverCard openDelay={100} closeDelay={100}>
                    <HoverCardTrigger asChild className="hover:border-none">
                      <Info className="mr-1 size-3 stroke-muted-foreground" />
                    </HoverCardTrigger>
                    <HoverCardContent
                      className="w-[300px] p-3 font-mono text-xs tracking-tight"
                      side="left"
                      sideOffset={20}
                    >
                      <WorkflowReturnValueTooltip />
                    </HoverCardContent>
                  </HoverCard>
                  <span className="text-xs text-muted-foreground">
                    Define the data returned by the workflow.
                  </span>
                </div>
                <span className="text-xs text-muted-foreground">
                    If undefined, the entire workflow run context is returned.
                  </span>
                <Controller
                  name="returns"
                  control={form.control}
                  render={({ field }) => (
                    <CustomEditor
                      className="h-48 w-full"
                      defaultLanguage="yaml"
                      value={field.value}
                      onChange={field.onChange}
                    />
                  )}
                />
              </div>
            </AccordionContent>
          </AccordionItem>
          <AccordionItem value="workflow-returns">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <div className="flex items-center">
                <FileInputIcon className="mr-3 size-4" />
                <span>Static Inputs</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="flex flex-col space-y-4 px-4">
                <div className="flex items-center">
                  <HoverCard openDelay={100} closeDelay={100}>
                    <HoverCardTrigger asChild className="hover:border-none">
                      <Info className="mr-1 size-3 stroke-muted-foreground" />
                    </HoverCardTrigger>
                    <HoverCardContent
                      className="w-[300px] p-3 font-mono text-xs tracking-tight"
                      side="left"
                      sideOffset={20}
                    >
                      <StaticInputTooltip />
                    </HoverCardContent>
                  </HoverCard>
                  <span className="text-xs text-muted-foreground">
                    Define optional static inputs for the workflow.
                  </span>
                </div>
                <Controller
                  name="static_inputs"
                  control={form.control}
                  render={({ field }) => (
                    <CustomEditor
                      className="h-48 w-full"
                      defaultLanguage="yaml"
                      value={field.value}
                      onChange={field.onChange}
                    />
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

function StaticInputTooltip() {
  return (
    <div className="w-full space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground ">
        <span className="font-mono text-sm font-semibold">Static Inputs</span>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <div className="flex w-full flex-col items-center justify-between space-y-4 text-muted-foreground">
        <span>
          Fixed key-value pairs passed into every action input and workflow run.
        </span>
        <span className="w-full text-muted-foreground">
          Usage example in expressions:
        </span>
      </div>
      <div className="rounded-md border bg-muted-foreground/10 p-2">
        <pre className="text-xs text-foreground/70">
          {"${{ INPUTS.my_static_key }}"}
        </pre>
      </div>
    </div>
  )
}

function WorkflowReturnValueTooltip() {
  return (
    <div className="flex w-full flex-col space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground">
        <span className="font-mono text-sm font-semibold">
          Output Schema
        </span>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <span className="w-full text-muted-foreground">
        Define the data returned by the workflow.
        Accepts static values and expressions.
        Use expressions to reference specific action outputs.
      </span>
      <span className="w-full text-muted-foreground">
        Usage example in expressions:
      </span>
      <div className="rounded-md border bg-muted-foreground/10 p-2">
        <pre className="text-xs text-foreground/70">
          {"${{ ACTIONS.my_action.result }}"}
        </pre>
      </div>
    </div>
  )
}
