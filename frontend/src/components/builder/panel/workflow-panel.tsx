"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type React from "react"
import { useCallback } from "react"

import "@radix-ui/react-dialog"

import {
  FileSliders,
  Info,
  LayoutListIcon,
  Redo2Icon,
  Settings2Icon,
  ShapesIcon,
  Undo2Icon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  type ExpectedField,
  type WorkflowRead,
  type WorkflowUpdate,
} from "@/client"
import { ControlledYamlField } from "@/components/builder/panel/action-panel-fields"
import { CopyButton } from "@/components/copy-button"
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
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  isRequestValidationErrorArray,
  type TracecatApiError,
} from "@/lib/errors"
import { useWorkflow } from "@/providers/workflow"

const workflowUpdateFormSchema = z.object({
  title: z
    .string()
    .min(1, { message: "Name is required" })
    .max(100, { message: "Name cannot exceed 100 characters" }),
  description: z
    .string()
    .max(1000, { message: "Description cannot exceed 1000 characters" })
    .optional(),
  alias: z
    .string()
    .max(100, { message: "Alias cannot exceed 100 characters" })
    .nullish(),
  // Config fields
  environment: z
    .string()
    .max(100, { message: "Environment cannot exceed 100 characters" })
    .optional(),
  timeout: z
    .number()
    .min(0, { message: "Timeout must be at least 0 seconds" })
    .max(1209600, {
      message: "Timeout cannot exceed 14 days (1209600 seconds)",
    })
    .optional(),
  /* Input Schema */
  expects: z
    .record(
      z.string(),
      z
        .object({
          type: z.string(),
          description: z.string().nullable().optional(),
          default: z.unknown().nullable().optional(),
        })
        .refine((val): val is ExpectedField => true)
    )
    .nullish(),
  returns: z.unknown().nullish(),
  /* Error Handler */
  error_handler: z
    .string()
    .max(100, { message: "Error handler cannot exceed 100 characters" })
    .nullish(),
})

type WorkflowUpdateForm = z.infer<typeof workflowUpdateFormSchema>
export function WorkflowPanel({
  workflow,
}: {
  workflow: WorkflowRead
}): React.JSX.Element {
  const { updateWorkflow } = useWorkflow()
  const methods = useForm<WorkflowUpdateForm>({
    resolver: zodResolver(workflowUpdateFormSchema),
    defaultValues: {
      title: workflow.title || "",
      description: workflow.description || "",
      alias: workflow.alias,
      environment: workflow.config?.environment || "default",
      timeout: workflow.config?.timeout || 0,
      expects: workflow.expects || undefined,
      returns: workflow.returns,
      error_handler: workflow.error_handler || "",
    },
  })

  const onSubmit = useCallback(
    async (values: WorkflowUpdateForm) => {
      console.log("Saving changes...", values)
      try {
        const updateData: WorkflowUpdate = {
          ...values,
          config: {
            environment: values.environment,
            timeout: values.timeout,
          },
        }

        await updateWorkflow(updateData)
      } catch (error) {
        if (error instanceof ApiError) {
          const apiError = error as TracecatApiError
          console.error("Application failed to validate action", apiError.body)

          // Set form errors from API validation errors
          if (isRequestValidationErrorArray(apiError.body.detail)) {
            const details = apiError.body.detail
            details.forEach((detail) => {
              methods.setError(detail.loc[1] as keyof WorkflowUpdateForm, {
                message: detail.msg,
              })
            })
          } else {
            console.error("Validation failed, unknown error", error)
            methods.setError("root", {
              message: `Validation failed, unknown error: ${JSON.stringify(
                apiError.body.detail
              )}`,
            })
          }
        } else {
          console.error("Validation failed, unknown error", error)
        }
      }
    },
    [updateWorkflow, methods]
  )

  const onPanelBlur = useCallback(async () => {
    // Save whenever focus changes, regardless of where it's going
    const values = methods.getValues()

    // Parse values through zod schema first
    const result = await workflowUpdateFormSchema.safeParseAsync(values)
    if (!result.success) {
      console.error("Validation failed:", result.error)
      // Set form errors with field name and message
      Object.entries(result.error.formErrors.fieldErrors).forEach(
        ([fieldName, error]) => {
          methods.setError(fieldName as keyof WorkflowUpdateForm, {
            type: "validation",
            message: error[0] || "Invalid value",
          })
        }
      )
      return
    }
    await onSubmit(result.data)
  }, [methods, onSubmit])

  return (
    <div onBlur={onPanelBlur}>
      <Tabs defaultValue="workflow-settings" className="w-full">
        <Form {...methods}>
          <form
            onSubmit={methods.handleSubmit(onSubmit)}
            className="flex flex-col overflow-auto"
          >
            <div className="w-full min-w-[30rem]">
              <div className="mt-0.5 flex items-center justify-start">
                <TabsList className="h-8 justify-start rounded-none bg-transparent p-0">
                  <TabsTrigger
                    className="flex h-full min-w-28 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="workflow-settings"
                  >
                    <LayoutListIcon className="mr-2 size-4" />
                    <span>General</span>
                  </TabsTrigger>
                  <TabsTrigger
                    className="h-full min-w-28 rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="workflow-schema"
                  >
                    <ShapesIcon className="mr-2 size-4" />
                    <span>Schema</span>
                  </TabsTrigger>
                </TabsList>
              </div>
              <Separator />
              <div className="w-full overflow-x-auto">
                <TabsContent value="workflow-settings">
                  <Accordion
                    type="multiple"
                    defaultValue={["workflow-settings", "workflow-config"]}
                  >
                    <AccordionItem value="workflow-settings">
                      <AccordionTrigger className="px-4 text-xs font-bold">
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
                            name="error_handler"
                            render={({ field }) => (
                              <FormItem>
                                <div className="flex items-center gap-2">
                                  <FormLabel className="flex items-center text-xs">
                                    <HoverCard openDelay={100} closeDelay={100}>
                                      <HoverCardTrigger
                                        asChild
                                        className="hover:border-none"
                                      >
                                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                                      </HoverCardTrigger>
                                      <HoverCardContent
                                        className="w-[300px] p-3 font-mono text-xs tracking-tight"
                                        side="right"
                                        sideOffset={20}
                                      >
                                        <div className="w-full space-y-4">
                                          <div className="flex w-full items-center justify-between text-muted-foreground">
                                            <span className="font-mono text-sm font-semibold">
                                              Error handler workflow
                                            </span>
                                            <span className="text-xs text-muted-foreground/80">
                                              (optional)
                                            </span>
                                          </div>
                                          <span className="text-muted-foreground">
                                            The ID or alias of another workflow
                                            to run when this workflow encounters
                                            an error.
                                          </span>
                                        </div>
                                      </HoverCardContent>
                                    </HoverCard>
                                    <span>Error workflow</span>
                                  </FormLabel>
                                  {field.value && (
                                    <CopyButton
                                      value={field.value}
                                      toastMessage="Copied error workflow to clipboard"
                                    />
                                  )}
                                </div>
                                <FormControl>
                                  <Input
                                    className="text-xs"
                                    placeholder="Workflow to run when an error occurs."
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
                            name="alias"
                            render={({ field }) => (
                              <FormItem>
                                <div className="flex items-center gap-2">
                                  <FormLabel className="flex items-center text-xs">
                                    <HoverCard openDelay={100} closeDelay={100}>
                                      <HoverCardTrigger
                                        asChild
                                        className="hover:border-none"
                                      >
                                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                                      </HoverCardTrigger>
                                      <HoverCardContent
                                        className="w-[300px] p-3 font-mono text-xs tracking-tight"
                                        side="right"
                                        sideOffset={20}
                                      >
                                        <div className="w-full space-y-4">
                                          <div className="flex w-full items-center justify-between text-muted-foreground">
                                            <span className="font-mono text-sm font-semibold">
                                              Workflow alias
                                            </span>
                                            <span className="text-xs text-muted-foreground/80">
                                              (optional)
                                            </span>
                                          </div>
                                          <span className="text-muted-foreground">
                                            A unique identifier for the workflow
                                            that can be used instead of the
                                            workflow ID. Must be unique within
                                            your workspace.
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
                                    placeholder="Unique identifier for this workflow."
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
                      <AccordionTrigger className="px-4 text-xs font-bold">
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
                                          # The maximum number of seconds to
                                          wait for the workflow to complete. If
                                          set to 0, the workflow will not
                                          timeout. Defaults to 0 (unlimited).
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
                          <FormField
                            name="environment"
                            control={methods.control}
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel className="text-xs">
                                  Environment
                                </FormLabel>
                                <FormControl>
                                  <Input
                                    className="text-xs"
                                    placeholder="default"
                                    {...field}
                                  />
                                </FormControl>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                          <FormField
                            name="timeout"
                            control={methods.control}
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel className="text-xs">
                                  Timeout (seconds)
                                </FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    className="text-xs"
                                    placeholder="0 (unlimited)"
                                    value={field.value || ""}
                                    onChange={(e) =>
                                      field.onChange(
                                        e.target.value
                                          ? parseInt(e.target.value)
                                          : undefined
                                      )
                                    }
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
                </TabsContent>
                <TabsContent value="workflow-schema">
                  <Accordion
                    type="multiple"
                    defaultValue={["workflow-expects", "workflow-returns"]}
                  >
                    <AccordionItem value="workflow-expects">
                      <AccordionTrigger className="px-4 text-xs font-bold">
                        <div className="flex items-center">
                          <Redo2Icon className="mr-3 size-4" />
                          <span>Input schema</span>
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
                            If undefined, the workflow will not validate the
                            trigger inputs.
                          </span>
                          <ControlledYamlField fieldName="expects" />
                        </div>
                      </AccordionContent>
                    </AccordionItem>
                    <AccordionItem value="workflow-returns">
                      <AccordionTrigger className="px-4 text-xs font-bold">
                        <div className="flex items-center">
                          <Undo2Icon className="mr-3 size-4" />
                          <span>Output schema</span>
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
                            If undefined, only the workflow run metadata is
                            returned.
                          </span>
                          <ControlledYamlField fieldName="returns" />
                        </div>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                </TabsContent>
              </div>
            </div>
          </form>
        </Form>
      </Tabs>
    </div>
  )
}

function WorkflowReturnValueTooltip() {
  return (
    <div className="flex w-full flex-col space-y-4">
      <div className="flex w-full items-center justify-between text-muted-foreground">
        <span className="font-mono text-sm font-semibold">Output schema</span>
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
        <span className="font-mono text-sm font-semibold">Input schema</span>
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
