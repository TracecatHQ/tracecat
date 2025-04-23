"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { ApiError, RegistryActionValidateResponse } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  ChevronDownIcon,
  DownloadIcon,
  MoreHorizontal,
  PlayIcon,
  SaveIcon,
  SquarePlay,
  WorkflowIcon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import YAML from "yaml"
import { z } from "zod"

import { TracecatApiError } from "@/lib/errors"
import {
  useCreateManualWorkflowExecution,
  useOrgAppSettings,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { DynamicCustomEditor } from "@/components/editor/dynamic"
import { ExportMenuItem } from "@/components/export-workflow-dropdown-item"
import { Spinner } from "@/components/loading/spinner"

export function WorkbenchNav() {
  const {
    workflow,
    isLoading: workflowLoading,
    commitWorkflow,
    validationErrors,
    setValidationErrors,
  } = useWorkflow()

  const { workspaceId, workspace, workspaceLoading } = useWorkspace()

  const handleCommit = async () => {
    console.log("Saving changes...")
    try {
      const response = await commitWorkflow()
      const { status, errors } = response
      if (status === "failure") {
        setValidationErrors(errors || null)
      } else {
        setValidationErrors(null)
      }
    } catch (error) {
      console.error("Failed to save workflow:", error)
    }
  }

  if (!workflow || workflowLoading || !workspace || workspaceLoading) {
    return null
  }

  const manualTriggerDisabled = workflow.version === null
  const workflowsPath = `/workspaces/${workspaceId}/workflows`
  return (
    <div className="flex w-full items-center">
      <div className="mr-4 min-w-0 flex-1">
        <Breadcrumb>
          <BreadcrumbList className="flex-nowrap overflow-hidden whitespace-nowrap">
            <BreadcrumbItem>
              <BreadcrumbLink href={workflowsPath}>
                {workspace.name}
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator className="shrink-0 font-semibold">
              {"/"}
            </BreadcrumbSeparator>
            <BreadcrumbItem>
              <span>{workflow.title}</span>
              {workflow.alias && (
                <Badge
                  variant="secondary"
                  className="font-mono text-xs font-normal tracking-tighter text-muted-foreground hover:cursor-default"
                >
                  {workflow.alias}
                </Badge>
              )}
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
      </div>

      <div className="flex items-center justify-end space-x-6">
        {/* Workflow tabs */}
        <TabSwitcher workflowId={workflow.id} />
        {/* Workflow manual trigger */}

        <WorkflowManualTrigger
          disabled={manualTriggerDisabled}
          workflowId={workflow.id}
        />
        {/* Save button */}
        <div className="flex items-center space-x-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                onClick={handleCommit}
                className={cn(
                  "h-7 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white",
                  validationErrors &&
                    "border-rose-400 text-rose-400 hover:bg-transparent hover:text-rose-500"
                )}
              >
                {validationErrors ? (
                  <AlertTriangleIcon className="mr-2 size-4 fill-red-500 stroke-white" />
                ) : (
                  <SaveIcon className="mr-2 size-4" />
                )}
                Save
              </Button>
            </TooltipTrigger>

            <TooltipContent side="bottom" className="w-fit text-xs shadow-lg">
              {validationErrors ? (
                <div className="space-y-2 rounded-md border border-rose-400 bg-rose-100 p-2 font-mono tracking-tighter">
                  <span className="text-xs font-bold text-rose-500">
                    Validation Errors
                  </span>
                  <div className="mt-1 space-y-1">
                    {validationErrors.map((error, index) => (
                      <div className="space-y-2" key={index}>
                        <Separator className="bg-rose-400" />
                        <ErrorMessage
                          key={index}
                          {...error}
                          className="text-rose-500"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <span>
                  Save workflow v{(workflow.version || 0) + 1} with your
                  changes.
                </span>
              )}
            </TooltipContent>
          </Tooltip>

          <Badge
            variant="secondary"
            className="h-7 text-xs font-normal text-muted-foreground hover:cursor-default"
          >
            {workflow.version ? `v${workflow.version}` : "Draft"}
          </Badge>
        </div>

        {/* Workflow options */}
        <WorkbenchNavOptions
          workspaceId={workspaceId}
          workflowId={workflow.id}
        />
      </div>
    </div>
  )
}

const isErrorDetailEmpty = (detail: unknown): boolean => {
  if (detail === null || detail === undefined) return true
  if (typeof detail === "object" && Object.keys(detail).length === 0)
    return true
  return false
}

function ErrorMessage({
  message,
  detail,
  className,
}: RegistryActionValidateResponse & React.HTMLAttributes<HTMLPreElement>) {
  // Replace newline characters with <br /> tags
  const formattedMessage = message.split("\n").map((line, index) => (
    <React.Fragment key={index}>
      {line}
      <br />
    </React.Fragment>
  ))

  return (
    <pre
      className={cn("overflow-auto whitespace-pre-wrap text-wrap", className)}
    >
      {formattedMessage}
      {!isErrorDetailEmpty(detail) && (
        <React.Fragment>
          <br />
          {YAML.stringify(detail, null, 2)}
        </React.Fragment>
      )}
    </pre>
  )
}

function TabSwitcher({ workflowId }: { workflowId: string }) {
  const pathname = usePathname()
  const { workspaceId } = useWorkspace()
  let leafRoute: string = "workflow"
  if (pathname.includes("executions")) {
    leafRoute = "executions"
  }

  const workbenchPath = `/workspaces/${workspaceId}/workflows/${workflowId}`

  return (
    <Tabs value={leafRoute}>
      <TabsList className="grid h-8 w-full grid-cols-2">
        <TabsTrigger className="w-full px-2 py-0" value="workflow" asChild>
          <Link href={workbenchPath} className="size-full text-xs" passHref>
            <WorkflowIcon className="mr-2 size-4" />
            <span>Workflow</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full px-2 py-0" value="executions" asChild>
          <Link
            href={`${workbenchPath}/executions`}
            className="size-full text-xs"
            passHref
          >
            <SquarePlay className="mr-2 size-4" />
            <span>Runs</span>
          </Link>
        </TabsTrigger>
      </TabsList>
    </Tabs>
  )
}

const workflowControlsFormSchema = z.object({
  payload: z.string().superRefine((val, ctx) => {
    try {
      JSON.parse(val)
    } catch (error) {
      if (error instanceof Error) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `Invalid JSON format: ${error.message}`,
        })
      } else {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Invalid JSON format: Unknown error occurred",
        })
      }
    }
  }),
})
type TWorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

function WorkflowManualTrigger({
  disabled = true,
  workflowId,
}: {
  disabled: boolean
  workflowId: string
}) {
  const { expandSidebarAndFocusEvents, setCurrentExecutionId } =
    useWorkflowBuilder()
  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(workflowId)
  const [open, setOpen] = React.useState(false)
  const { workspaceId } = useWorkspace()
  const [lastTriggerInput, setLastTriggerInput] = React.useState<string | null>(
    null
  )
  const [manualTriggerErrors, setManualTriggerErrors] = React.useState<Record<
    string,
    string
  > | null>(null)
  const [isTriggering, setIsTriggering] = React.useState(false)
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      payload: lastTriggerInput || '{"sampleWebhookParam": "sampleValue"}',
    },
  })

  const runWorkflow = async ({ payload }: Partial<TWorkflowControlsForm>) => {
    if (disabled || createExecutionIsPending) return
    setIsTriggering(true)
    setTimeout(() => setIsTriggering(false), 1000)
    setManualTriggerErrors(null)
    try {
      const result = await createExecution({
        workflow_id: workflowId,
        inputs: payload ? JSON.parse(payload) : undefined,
      })

      // Store the execution ID directly
      if (result && result.wf_exec_id) {
        setCurrentExecutionId(result.wf_exec_id)
      }

      // Expand sidebar immediately
      expandSidebarAndFocusEvents()
    } catch (error) {
      if (error instanceof ApiError) {
        const tracecatError = error as TracecatApiError<Record<string, string>>
        console.error("Error details", tracecatError.body.detail)
        setManualTriggerErrors(tracecatError.body.detail)
      }
    }
  }

  const runWithPayload = async ({ payload }: TWorkflowControlsForm) => {
    // Make the API call to start the workflow
    setLastTriggerInput(payload)
    try {
      await runWorkflow({ payload })
    } finally {
      setOpen(false)
    }
  }

  const executionPending = createExecutionIsPending || isTriggering
  return (
    <Form {...form}>
      <div className="flex h-7 gap-px rounded-lg border border-input bg-emerald-400">
        {/* Main Button */}
        <Button
          type="button"
          variant="outline"
          className={cn(
            "h-full gap-2 rounded-r-none border-none px-3 py-0 text-xs font-bold",
            manualTriggerErrors
              ? "text-rose-400 hover:text-rose-500"
              : "bg-emerald-500 text-white hover:bg-emerald-400 hover:text-white"
          )}
          disabled={disabled || executionPending}
          onClick={() => runWorkflow({ payload: undefined })}
        >
          {executionPending ? (
            <Spinner className="size-3" segmentColor="#fff" />
          ) : manualTriggerErrors ? (
            <AlertTriangleIcon className="size-3 fill-red-500 stroke-white" />
          ) : (
            <PlayIcon className="size-3 fill-white stroke-white" />
          )}
          <span>Run</span>
        </Button>
        {/* Dropdown Button */}
        <Tooltip delayDuration={500}>
          <Popover
            open={open && !disabled}
            onOpenChange={(newOpen) => !disabled && setOpen(newOpen)}
          >
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  className={cn(
                    "h-full w-7 rounded-l-none border-none px-1 py-0 text-xs font-bold",
                    manualTriggerErrors
                      ? "text-rose-400 hover:text-rose-500"
                      : "bg-emerald-500 text-white hover:bg-emerald-400 hover:text-white"
                  )}
                  disabled={disabled || executionPending}
                >
                  <ChevronDownIcon className="size-3" />
                </Button>
              </PopoverTrigger>
            </TooltipTrigger>
            <PopoverContent className="w-fit p-3">
              <form onSubmit={form.handleSubmit(runWithPayload)}>
                <div className="flex h-fit flex-col">
                  <span className="mb-2 text-xs text-muted-foreground">
                    Edit the JSON payload below.
                  </span>
                  <FormField
                    control={form.control}
                    name="payload"
                    render={({ field }) => (
                      <FormItem>
                        <FormControl>
                          <DynamicCustomEditor
                            className="max-h-[50rem] min-h-60 min-w-[30rem] max-w-[50rem] resize overflow-auto"
                            defaultLanguage="yaml-extended"
                            value={field.value}
                            onChange={field.onChange}
                            workspaceId={workspaceId}
                            workflowId={workflowId}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <Button
                    type="submit"
                    variant="default"
                    disabled={executionPending}
                    className="group mt-2 flex h-7 items-center bg-emerald-500 px-3 py-0 text-xs text-white hover:bg-emerald-500/80 hover:text-white"
                  >
                    {executionPending ? (
                      <Spinner className="mr-2 size-3" />
                    ) : (
                      <PlayIcon className="mr-2 size-3 fill-white stroke-white" />
                    )}
                    <span>{executionPending ? "Starting..." : "Run"}</span>
                  </Button>
                </div>
              </form>
            </PopoverContent>
          </Popover>
          <TooltipContent
            side="bottom"
            className={cn("text-xs shadow-lg", manualTriggerErrors && "p-0")}
          >
            {manualTriggerErrors ? (
              <div className="space-y-2 overflow-auto rounded-md border border-rose-400 bg-rose-100 p-2 font-mono tracking-tighter">
                <span className="text-xs font-bold text-rose-500">
                  Trigger Validation Errors
                </span>
                <div className="mt-1 space-y-1">
                  <pre className="text-wrap text-rose-500">
                    {YAML.stringify(manualTriggerErrors)}
                  </pre>
                </div>
              </div>
            ) : disabled ? (
              "Please save changes to enable manual trigger."
            ) : executionPending ? (
              "Starting workflow execution..."
            ) : (
              "Run the workflow with trigger inputs."
            )}
          </TooltipContent>
        </Tooltip>
      </div>
    </Form>
  )
}

function WorkbenchNavOptions({
  workspaceId,
  workflowId,
}: {
  workspaceId: string
  workflowId: string
}) {
  const { appSettings } = useOrgAppSettings()
  const enabledExport = appSettings?.app_workflow_export_enabled
  return (
    <Dialog>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon">
            <MoreHorizontal className="size-4" />
            <span className="sr-only">More</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <ExportMenuItem
            enabledExport={enabledExport}
            format="yaml"
            workspaceId={workspaceId}
            workflowId={workflowId}
            icon={<DownloadIcon className="mr-2 size-4" />}
          />
          <ExportMenuItem
            enabledExport={enabledExport}
            format="json"
            workspaceId={workspaceId}
            workflowId={workflowId}
            icon={<DownloadIcon className="mr-2 size-4" />}
          />
        </DropdownMenuContent>
      </DropdownMenu>
    </Dialog>
  )
}
