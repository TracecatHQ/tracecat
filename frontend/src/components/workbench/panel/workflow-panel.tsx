"use client"

import React, { useCallback, useState } from "react"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { ApiError, WorkflowRead } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import {
  FileInputIcon,
  FileSliders,
  Info,
  LayoutListIcon,
  Redo2Icon,
  Settings2Icon,
  ShapesIcon,
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
import {
  Form,
  FormControl,
  FormDescription,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
import { DynamicCustomEditor } from "@/components/editor/dynamic"

const workflowUpdateFormSchema = z.object({
  title: z.string(),
  description: z.string(),
  alias: z.string().nullish(),
  config: z.string().transform((val, ctx) => {
    try {
      return YAML.parse(val) || {}
    } catch (error) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Invalid YAML format. Please check workflow definition for errors.",
      })
      return z.NEVER
    }
  }),
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
  /* Input Schema */
  expects: z.string().transform((val, ctx) => {
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
  /* Output Schema */
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
  /* Error Handler */
  error_handler: z.string().nullish(),
})

type WorkflowUpdateForm = z.infer<typeof workflowUpdateFormSchema>

export function WorkflowPanel({
  workflow,
}: {
  workflow: WorkflowRead
}): React.JSX.Element {
  const { workspaceId } = useWorkspace()
  const { workflowId } = useWorkflow()
  const [saveState, setSaveState] = useState<"idle" | "saving" | "success">(
    "idle"
  )

  const { updateWorkflow } = useWorkflow()
  const methods = useForm<WorkflowUpdateForm>({
    resolver: zodResolver(workflowUpdateFormSchema),
    defaultValues: {
      title: workflow.title || "",
      description: workflow.description || "",
      alias: workflow.alias,
      config: isEmptyObjectOrNullish(workflow.config)
        ? YAML.stringify({
            environment: null,
          })
        : YAML.stringify(workflow.config),
      static_inputs: isEmptyObjectOrNullish(workflow.static_inputs)
        ? ""
        : YAML.stringify(workflow.static_inputs),
      expects: isEmptyObjectOrNullish(workflow.expects)
        ? ""
        : YAML.stringify(workflow.expects),
      returns: isEmptyObjectOrNullish(workflow.returns)
        ? ""
        : YAML.stringify(workflow.returns),
      error_handler: workflow.error_handler || "",
    },
  })
  console.log("workflow alias", workflow.alias)

  const onSubmit = async (values: WorkflowUpdateForm) => {
    console.log("Saving changes...", values)
    try {
      await updateWorkflow(values)
    } catch (error) {
      if (error instanceof ApiError) {
        console.error("Error saving changes", error)
        toast({
          title: "Error saving changes",
          description: error.message,
          variant: "destructive",
        })
      }
    }
  }

  const onPanelBlur = useCallback(
    async (event: React.FocusEvent) => {
      // Save whenever focus changes, regardless of where it's going
      const values = methods.getValues()
      // Parse values through zod schema first
      const result = await workflowUpdateFormSchema.safeParseAsync(values)
      if (!result.success) {
        console.error("Validation failed:", result.error)
        return
      }
      await onSubmit(result.data)
    },
    [methods, onSubmit]
  )

  return (
    <div onBlur={onPanelBlur}>
      <Tabs defaultValue="workflow-settings">
        <Form {...methods}>
          <form
            onSubmit={methods.handleSubmit(onSubmit)}
            className="flex max-w-full flex-col overflow-auto"
          >
            <div className="mt-2 flex items-center justify-start">
              <TabsList className="grid h-8 grid-cols-3 rounded-none bg-transparent p-0">
                <TabsTrigger
                  className="size-full w-full min-w-[120px] rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="workflow-settings"
                >
                  <LayoutListIcon className="mr-2 size-4" />
                  <span>General</span>
                </TabsTrigger>
                <TabsTrigger
                  className="size-full w-full min-w-[120px] rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="workflow-schema"
                >
                  <ShapesIcon className="mr-2 size-4" />
                  <span>Schema</span>
                </TabsTrigger>
                <TabsTrigger
                  className="size-full w-full min-w-[120px] rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="workflow-static-inputs"
                >
                  <FileInputIcon className="mr-2 size-4" />
                  <span>Static Inputs</span>
                </TabsTrigger>
              </TabsList>
            </div>
            <Separator />
            {/* Workflow Settings */}
            <TabsContent value="workflow-settings">
              <Accordion
                type="multiple"
                defaultValue={["workflow-settings", "workflow-config"]}
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
                        control={methods.control}
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
                        control={methods.control}
                        name="description"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-xs">
                              Description
                            </FormLabel>
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

                      <FormField
                        control={methods.control}
                        name="alias"
                        render={({ field }) => (
                          <FormItem>
                            <div className="flex items-center gap-2">
                              <FormLabel className="text-xs flex items-center">
                                <HoverCard openDelay={100} closeDelay={100}>
                                  <HoverCardTrigger asChild className="hover:border-none">
                                    <Info className="mr-1 size-3 stroke-muted-foreground" />
                                  </HoverCardTrigger>
                                  <HoverCardContent
                                    className="w-[300px] p-3 font-mono text-xs tracking-tight"
                                    side="right"
                                    sideOffset={20}
                                  >
                                    <div className="w-full space-y-4">
                                      <div className="flex w-full items-center justify-between text-muted-foreground">
                                        <span className="font-mono text-sm font-semibold">Workflow alias</span>
                                        <span className="text-xs text-muted-foreground/80">(optional)</span>
                                      </div>
                                      <span className="text-muted-foreground">
                                        A unique identifier for the workflow that can be used instead of the workflow ID.
                                        Must be unique within your workspace.
                                      </span>
                                    </div>
                                  </HoverCardContent>
                                </HoverCard>
                                <span>Alias</span>
                              </FormLabel>
                              {field.value && (
                                <CopyButton
                                  value={field.value}
                                  toastMessage="Copied workflow alias to clipboard"
                                />
                              )}
                            </div>
                            <FormControl>
                              <Input
                                className="text-xs"
                                {...field}
                                value={field.value || ""}
                                onChange={field.onChange}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={methods.control}
                        name="error_handler"
                        render={({ field }) => (
                          <FormItem>
                            <div className="flex items-center gap-2">
                              <FormLabel className="text-xs">
                                <span>Error Handler</span>
                              </FormLabel>
                              {field.value && (
                                <CopyButton
                                  value={field.value}
                                  toastMessage="Copied workflow error handler to clipboard"
                                />
                              )}
                            </div>
                            <FormDescription className="text-xs">
                              The workflow ID or alias of the error handler
                              workflow.
                            </FormDescription>
                            <FormControl>
                              <Input
                                className="text-xs"
                                placeholder="The workflow ID or alias of the error handler workflow"
                                {...field}
                                value={field.value || ""}
                                onChange={field.onChange}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <div className="space-y-2">
                        <FormLabel className="flex items-center gap-2 text-xs">
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
                <AccordionItem value="workflow-config">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <FileSliders className="mr-3 size-4" />
                      <span>Configuration</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="flex flex-col space-y-4 px-4">
                      <div className="flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <Info className="mr-1 size-3 stroke-muted-foreground" />
                          </HoverCardTrigger>
                          {/* Config tooltip */}
                          <HoverCardContent
                            className="w-[300px] p-3 font-mono text-xs tracking-tight"
                            side="left"
                            sideOffset={20}
                          >
                            <div className="w-full space-y-4">
                              <div className="flex w-full items-center justify-between text-muted-foreground ">
                                <span className="font-mono text-sm font-semibold">
                                  Runtime configuration
                                </span>
                                <span className="text-xs text-muted-foreground/80">
                                  (optional)
                                </span>
                              </div>
                              <div className="flex w-full flex-col items-center justify-between space-y-4 text-muted-foreground">
                                <span>
                                  Configuration that modifies the runtime
                                  behavior of services.
                                </span>
                              </div>
                              {/* Schema is hardcoded here for now */}
                              <div className="space-y-2">
                                <span className="w-full font-semibold text-muted-foreground">
                                  Fields
                                </span>
                                <pre className="space-y-2 text-wrap rounded-md border bg-muted-foreground/10 p-2 text-xs text-foreground/70">
                                  <div>
                                    <b>environment</b>
                                    {": string | null"}
                                    <p className="text-muted-foreground">
                                      # The workflow&apos;s target execution
                                      environment. Defaults to null.
                                    </p>
                                  </div>
                                  <div>
                                    <b>timeout</b>
                                    {": float"}
                                    <p className="text-muted-foreground">
                                      # The maximum number of seconds to wait
                                      for the workflow to complete. Defaults to
                                      300 seconds (5 minutes).
                                    </p>
                                  </div>
                                </pre>
                              </div>
                            </div>
                          </HoverCardContent>
                        </HoverCard>
                        <span className="text-xs text-muted-foreground">
                          Define the runtime configuration for the workflow.
                        </span>
                      </div>
                      <Controller
                        name="config"
                        control={methods.control}
                        render={({ field }) => (
                          <DynamicCustomEditor
                            className="h-48 w-full"
                            defaultLanguage="yaml-extended"
                            value={field.value}
                            onChange={field.onChange}
                            workspaceId={workspaceId}
                            workflowId={workflowId}
                          />
                        )}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </TabsContent>
            <TabsContent value="workflow-schema">
              <Accordion
                type="multiple"
                defaultValue={["workflow-expects", "workflow-returns"]}
              >
                <AccordionItem value="workflow-expects">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <Redo2Icon className="mr-3 size-4" />
                      <span className="capitalize">Input Schema</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="flex flex-col space-y-4 px-4">
                      <div className="flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <Info className="mr-1 size-3 stroke-muted-foreground" />
                          </HoverCardTrigger>
                          <HoverCardContent
                            className="w-[300px] p-3 font-mono text-xs tracking-tight"
                            side="left"
                            sideOffset={20}
                          >
                            <WorkflowInputSchemaTooltip />
                          </HoverCardContent>
                        </HoverCard>
                        <span className="text-xs text-muted-foreground">
                          Define the schema for the workflow trigger inputs.
                        </span>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        If undefined, the workflow will not validate the trigger
                        inputs.
                      </span>
                      <Controller
                        name="expects"
                        control={methods.control}
                        render={({ field }) => (
                          <DynamicCustomEditor
                            className="h-48 w-full"
                            defaultLanguage="yaml-extended"
                            value={field.value}
                            onChange={field.onChange}
                            workspaceId={workspaceId}
                            workflowId={workflowId}
                          />
                        )}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
                <AccordionItem value="workflow-returns">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <Undo2Icon className="mr-3 size-4" />
                      <span className="capitalize">Output Schema</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="flex flex-col space-y-4 px-4">
                      <div className="flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
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
                        If undefined, the entire workflow run context is
                        returned.
                      </span>
                      <Controller
                        name="returns"
                        control={methods.control}
                        render={({ field }) => (
                          <DynamicCustomEditor
                            className="h-48 w-full"
                            defaultLanguage="yaml-extended"
                            value={field.value}
                            onChange={field.onChange}
                            workspaceId={workspaceId}
                            workflowId={workflowId}
                          />
                        )}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </TabsContent>
            <TabsContent value="workflow-static-inputs">
              <Accordion
                type="multiple"
                defaultValue={["workflow-static-inputs"]}
              >
                <AccordionItem value="workflow-static-inputs">
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
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
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
                        control={methods.control}
                        render={({ field }) => (
                          <DynamicCustomEditor
                            className="h-48 w-full"
                            defaultLanguage="yaml-extended"
                            value={field.value}
                            onChange={field.onChange}
                            workspaceId={workspaceId}
                            workflowId={workflowId}
                          />
                        )}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </TabsContent>
          </form>
        </Form>
      </Tabs>
    </div>
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
        <span className="font-mono text-sm font-semibold">Output Schema</span>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <span className="w-full text-muted-foreground">
        Define the data returned by the workflow. Accepts static values and
        expressions. Use expressions to reference specific action outputs.
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

const exampleExpects = `\
my_param:
  type: str
  description: This is a string
  default: My default value

my_list:
  type: list[int]
  description: This is a list of integers without a default value

my_union:
  type: str | int | None
  description: This is a union of a string, an integer, and None
`
function WorkflowInputSchemaTooltip() {
  return (
    <div className="flex w-full flex-col space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground">
        <span className="font-mono text-sm font-semibold">Input Schema</span>
        <span className="text-xs text-muted-foreground/80">(optional)</span>
      </div>
      <span className="w-full text-muted-foreground">
        Define the workflow input schema. This will be used to validate the
        trigger inputs to the workflow.
      </span>
      <span className="w-full text-muted-foreground">
        Passing a default value makes the field optional.
      </span>
      <span className="w-full text-muted-foreground">Usage example:</span>
      <div className="rounded-md border bg-muted-foreground/10 p-2">
        <pre className="text-wrap text-xs text-foreground/70">
          {exampleExpects}
        </pre>
      </div>
    </div>
  )
}
