"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type React from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import "@radix-ui/react-dialog"

import { FileTextIcon, Info, LayoutListIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  type ExpectedField_Input,
  type WorkflowRead,
  type WorkflowUpdate,
  workflowsValidateWorkflowEntrypoint,
} from "@/client"
import { ControlledYamlField } from "@/components/builder/panel/action-panel-fields"
import { CopyButton } from "@/components/copy-button"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
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
import {
  isRequestValidationErrorArray,
  type TracecatApiError,
} from "@/lib/errors"
import { useWorkflow } from "@/providers/workflow"

const createWorkflowUpdateFormSchema = (workspaceId: string) =>
  z
    .object({
      title: z
        .string()
        .trim()
        .min(3, { message: "Name must be at least 3 characters" })
        .max(100, { message: "Name cannot exceed 100 characters" }),
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
              type: z.string().min(1, { message: "Type is required" }),
              description: z.string().nullable().optional(),
              default: z.unknown().nullable().optional(),
            })
            .refine((val): val is ExpectedField_Input => true)
        )
        .nullish(),
      returns: z.unknown().nullish(),
      /* Error Handler */
      error_handler: z
        .string()
        .max(100, { message: "Error handler cannot exceed 100 characters" })
        .nullish(),
    })
    .superRefine(async (values, ctx) => {
      if (!values.expects || Object.keys(values.expects).length === 0) {
        return
      }

      try {
        const { valid, errors } = await workflowsValidateWorkflowEntrypoint({
          workspaceId,
          requestBody: {
            expects: values.expects,
          },
        })

        if (!valid) {
          const messages = errors?.flatMap((error) => {
            if (!error?.detail || !Array.isArray(error.detail)) {
              return []
            }
            return error.detail.map((detail) => detail.msg).filter(Boolean)
          })

          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["expects"],
            message:
              messages && messages.length > 0
                ? messages.join("\n")
                : "Invalid trigger input definition.",
          })
        }
      } catch (error) {
        console.error("Failed to validate trigger inputs", error)
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["expects"],
          message: "Failed to validate trigger inputs. Please try again.",
        })
      }
    })

type WorkflowUpdateFormSchema = ReturnType<
  typeof createWorkflowUpdateFormSchema
>
type WorkflowUpdateForm = z.infer<WorkflowUpdateFormSchema>

const workflowReadmeSchema = z
  .string()
  .max(1000, { message: "README cannot exceed 1000 characters" })

type WorkflowPanelTab = "workflow" | "readme"

export function WorkflowPanel({
  workflow,
}: {
  workflow: WorkflowRead
}): React.JSX.Element {
  const [activeTab, setActiveTab] = useState<WorkflowPanelTab>("workflow")

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <Tabs
        defaultValue="workflow"
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as WorkflowPanelTab)}
        className="flex h-full w-full flex-col"
      >
        <div className="w-full min-w-[30rem] shrink-0">
          <div className="flex items-center justify-start">
            <TabsList className="h-9 justify-start rounded-none bg-transparent p-0">
              <TabsTrigger
                className="flex h-full min-w-24 items-center justify-center rounded-none px-5 py text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="workflow"
              >
                <LayoutListIcon className="mr-2 size-4" />
                <span>Workflow</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-24 items-center justify-center rounded-none px-5 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value="readme"
              >
                <FileTextIcon className="mr-2 size-4" />
                <span>README</span>
              </TabsTrigger>
            </TabsList>
          </div>
          <Separator />
        </div>
        <div className="flex-1 overflow-auto">
          <TabsContent value="workflow" className="mt-0 h-full">
            <WorkflowSettingsPanel workflow={workflow} />
          </TabsContent>
          <TabsContent value="readme" className="mt-0 h-full">
            <WorkflowReadmePanel workflow={workflow} />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  )
}

function WorkflowSettingsPanel({
  workflow,
}: {
  workflow: WorkflowRead
}): React.JSX.Element {
  const { updateWorkflow, workspaceId } = useWorkflow()
  const workflowUpdateFormSchema = useMemo(
    () => createWorkflowUpdateFormSchema(workspaceId),
    [workspaceId]
  )
  const methods = useForm<WorkflowUpdateForm>({
    resolver: zodResolver(workflowUpdateFormSchema, undefined, {
      mode: "async",
    }),
    defaultValues: {
      title: workflow.title,
      alias: workflow.alias,
      environment: workflow.config?.environment || "default",
      timeout: workflow.config?.timeout || 0,
      // Use undefined for empty objects so the YAML editor shows empty instead of {}
      expects:
        workflow.expects && Object.keys(workflow.expects).length > 0
          ? workflow.expects
          : undefined,
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
    if (result.success) {
      methods.clearErrors()
      await onSubmit(result.data)
    } else {
      console.warn("Validation failed:", result.error)
      // Set form errors with field name and message
      Object.entries(result.error.formErrors.fieldErrors).forEach(
        ([fieldName, error]) => {
          methods.setError(fieldName as keyof WorkflowUpdateForm, {
            type: "validation",
            message: error[0] || "Invalid value",
          })
        }
      )
    }
  }, [methods, onSubmit, workflowUpdateFormSchema])

  return (
    <div onBlur={onPanelBlur} className="flex h-full flex-col">
      <Form {...methods}>
        <form
          onSubmit={methods.handleSubmit(onSubmit)}
          className="flex flex-1 flex-col overflow-auto"
        >
          {/* All other fields in one flat section */}
          <div className="flex flex-col gap-6 overflow-y-auto px-4 pb-32 pt-4">
            {/* Name */}
            <FormField
              control={methods.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="flex items-center text-xs">
                    <HoverCard openDelay={100} closeDelay={100}>
                      <HoverCardTrigger asChild className="hover:border-none">
                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="w-[300px] p-3 font-mono text-xs tracking-tight"
                        side="left"
                        sideOffset={20}
                      >
                        <div className="w-full space-y-4">
                          <div className="flex w-full items-center justify-between text-muted-foreground">
                            <span className="font-mono text-sm font-semibold">
                              Workflow name
                            </span>
                          </div>
                          <span className="text-muted-foreground">
                            A human-readable name for the workflow. Must be at
                            least 3 characters and less than 100 characters.
                          </span>
                        </div>
                      </HoverCardContent>
                    </HoverCard>
                    <span>Name</span>
                  </FormLabel>
                  <FormControl>
                    <Input
                      className="text-xs"
                      placeholder="Name your workflow..."
                      {...field}
                      value={field.value || ""}
                      onChange={field.onChange}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Alias */}
            <FormField
              control={methods.control}
              name="alias"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center gap-2">
                    <FormLabel className="flex items-center text-xs">
                      <HoverCard openDelay={100} closeDelay={100}>
                        <HoverCardTrigger asChild className="hover:border-none">
                          <Info className="mr-1 size-3 stroke-muted-foreground" />
                        </HoverCardTrigger>
                        <HoverCardContent
                          className="w-[300px] p-3 font-mono text-xs tracking-tight"
                          side="left"
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
                              A unique identifier for the workflow that can be
                              used instead of the workflow ID. Must be unique
                              within your workspace.
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
                      placeholder="Unique identifier for this workflow"
                      {...field}
                      value={field.value || ""}
                      onChange={field.onChange}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Error handler */}
            <FormField
              control={methods.control}
              name="error_handler"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center gap-2">
                    <FormLabel className="flex items-center text-xs">
                      <HoverCard openDelay={100} closeDelay={100}>
                        <HoverCardTrigger asChild className="hover:border-none">
                          <Info className="mr-1 size-3 stroke-muted-foreground" />
                        </HoverCardTrigger>
                        <HoverCardContent
                          className="w-[300px] p-3 font-mono text-xs tracking-tight"
                          side="left"
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
                              The ID or alias of another workflow to run when
                              this workflow encounters an error.
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

            {/* Environment */}
            <FormField
              name="environment"
              control={methods.control}
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="flex items-center text-xs">
                    <HoverCard openDelay={100} closeDelay={100}>
                      <HoverCardTrigger asChild className="hover:border-none">
                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="w-[300px] p-3 font-mono text-xs tracking-tight"
                        side="left"
                        sideOffset={20}
                      >
                        <div className="w-full space-y-4">
                          <div className="flex w-full items-center justify-between text-muted-foreground">
                            <span className="font-mono text-sm font-semibold">
                              Environment
                            </span>
                            <span className="text-xs text-muted-foreground/80">
                              (optional)
                            </span>
                          </div>
                          <span className="text-muted-foreground">
                            The workflow&apos;s target execution environment.
                            Defaults to &quot;default&quot;.
                          </span>
                        </div>
                      </HoverCardContent>
                    </HoverCard>
                    <span>Environment</span>
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

            {/* Timeout */}
            <FormField
              name="timeout"
              control={methods.control}
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="flex items-center text-xs">
                    <HoverCard openDelay={100} closeDelay={100}>
                      <HoverCardTrigger asChild className="hover:border-none">
                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="w-[300px] p-3 font-mono text-xs tracking-tight"
                        side="left"
                        sideOffset={20}
                      >
                        <div className="w-full space-y-4">
                          <div className="flex w-full items-center justify-between text-muted-foreground">
                            <span className="font-mono text-sm font-semibold">
                              Timeout
                            </span>
                            <span className="text-xs text-muted-foreground/80">
                              (optional)
                            </span>
                          </div>
                          <span className="text-muted-foreground">
                            The maximum number of seconds to wait for the
                            workflow to complete. If set to 0, the workflow will
                            not timeout. Defaults to 0 (unlimited).
                          </span>
                        </div>
                      </HoverCardContent>
                    </HoverCard>
                    <span>Timeout (seconds)</span>
                  </FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      className="text-xs"
                      placeholder="0 (unlimited)"
                      value={field.value || ""}
                      onChange={(e) =>
                        field.onChange(
                          e.target.value ? parseInt(e.target.value) : undefined
                        )
                      }
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Input schema */}
            <FormItem>
              <FormLabel className="flex items-center text-xs">
                <HoverCard openDelay={100} closeDelay={100}>
                  <HoverCardTrigger asChild className="hover:border-none">
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
                <span>Input schema</span>
              </FormLabel>
              <ControlledYamlField fieldName="expects" hideType />
            </FormItem>

            {/* Output schema */}
            <FormItem>
              <FormLabel className="flex items-center text-xs">
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
                <span>Output schema</span>
              </FormLabel>
              <ControlledYamlField fieldName="returns" hideType />
            </FormItem>
          </div>
        </form>
      </Form>
    </div>
  )
}

function WorkflowReadmePanel({
  workflow,
}: {
  workflow: WorkflowRead
}): React.JSX.Element {
  const { updateWorkflow } = useWorkflow()
  const [value, setValue] = useState(workflow.description ?? "")
  const [isSaving, setIsSaving] = useState(false)
  const savedValueRef = useRef(workflow.description ?? "")
  const containerRef = useRef<HTMLDivElement>(null)
  const isTooLong = value.length > 1000

  useEffect(() => {
    const nextValue = workflow.description ?? ""
    const previousValue = savedValueRef.current
    savedValueRef.current = nextValue
    setValue((current) => (current === previousValue ? nextValue : current))
  }, [workflow.description, workflow.id])

  const persistReadme = useCallback(async () => {
    if (isSaving) {
      return
    }

    const nextValue = value ?? ""
    if (nextValue === savedValueRef.current) {
      return
    }

    if (isTooLong) {
      return
    }

    const result = workflowReadmeSchema.safeParse(nextValue)
    if (!result.success) {
      return
    }

    setIsSaving(true)
    try {
      await updateWorkflow({ description: result.data })
      savedValueRef.current = result.data
    } finally {
      setIsSaving(false)
    }
  }, [isSaving, isTooLong, updateWorkflow, value])

  const handleContainerBlur = useCallback(
    (event: React.FocusEvent<HTMLDivElement>) => {
      const container = containerRef.current
      const nextTarget = event.relatedTarget as Node | null
      if (!container || !nextTarget || !container.contains(nextTarget)) {
        void persistReadme()
      }
    },
    [persistReadme]
  )

  const toolbarStatus = isTooLong ? (
    <span className="text-xs text-destructive">Max 1000 characters</span>
  ) : isSaving ? (
    <span className="text-xs text-muted-foreground">Saving...</span>
  ) : null

  return (
    <div
      ref={containerRef}
      onBlur={handleContainerBlur}
      className="flex h-full flex-col overflow-auto p-4"
    >
      <div className="mb-4 space-y-1">
        <h4 className="text-xs text-muted-foreground">
          Edit workflow description
        </h4>
      </div>
      <div className="min-h-[300px] flex-1 rounded-md border border-input bg-background [&_.simple-editor-content_.tiptap.ProseMirror.simple-editor]:p-3">
        <SimpleEditor
          value={value}
          onChange={setValue}
          onSave={() => void persistReadme()}
          placeholder="Type some markdown here to start writing your README."
          toolbarStatus={toolbarStatus}
          className="h-full"
          style={{ height: "100%" }}
        />
      </div>
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
