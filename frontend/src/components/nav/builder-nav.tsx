"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  ChevronDownIcon,
  CopyIcon,
  DownloadIcon,
  GitBranchIcon,
  LayersPlusIcon,
  MoreHorizontal,
  PlayIcon,
  SquarePlay,
  Trash2Icon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import React from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type {
  DSLValidationResult,
  ValidationDetail,
  ValidationResult,
} from "@/client"
import { ApiError } from "@/client"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { ExportMenuItem } from "@/components/export-workflow-dropdown-item"
import { Spinner } from "@/components/loading/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Kbd } from "@/components/ui/kbd"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ValidationErrorView } from "@/components/validation-errors"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import type { TracecatApiError } from "@/lib/errors"
import {
  useCreateDraftWorkflowExecution,
  useOrgAppSettings,
  useWorkflowManager,
} from "@/lib/hooks"
import { cn, copyToClipboard } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

export function BuilderNav() {
  const {
    workflow,
    isLoading: workflowLoading,
    commitWorkflow,
    publishWorkflow,
    validationErrors,
    setValidationErrors,
  } = useWorkflow()

  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading } = useWorkspaceDetails()
  const workflowTitle = workflow?.title ?? "Untitled workflow"

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

  // Always allow running - use draft endpoint when no committed version
  const manualTriggerDisabled = false

  return (
    <div className="flex w-full items-center">
      <div className="mr-4 min-w-0 flex-1">
        <Breadcrumb>
          <BreadcrumbList className="flex-nowrap overflow-hidden whitespace-nowrap">
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                <Link href={`/workspaces/${workspaceId}/workflows`}>
                  {workspace.name}
                </Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator className="shrink-0 font-semibold">
              {"/"}
            </BreadcrumbSeparator>
            <BreadcrumbItem>
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-sm text-foreground">
                  {workflowTitle}
                </span>
                {workflow.alias && (
                  <Badge
                    variant="secondary"
                    className="font-mono text-xs font-normal tracking-tighter text-muted-foreground hover:cursor-default"
                  >
                    {workflow.alias}
                  </Badge>
                )}
              </div>
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
        <WorkflowSaveActions
          workflow={workflow}
          validationErrors={validationErrors}
          onSave={handleCommit}
          onPublish={publishWorkflow}
        />

        {/* Workflow options */}
        <BuilderNavOptions workspaceId={workspaceId} workflowId={workflow.id} />
      </div>
    </div>
  )
}

function TabSwitcher({ workflowId }: { workflowId: string }) {
  const pathname = usePathname()
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const pendingNavKeyRef = React.useRef<"w" | "r" | null>(null)
  const pendingNavAtRef = React.useRef<number | null>(null)
  let leafRoute: string = "workflow"
  if (pathname && pathname.includes("executions")) {
    leafRoute = "executions"
  }

  const builderPath = `/workspaces/${workspaceId}/workflows/${workflowId}`
  const executionsPath = `${builderPath}/executions`
  const keyOnlyTooltipClassName = "border-0 bg-transparent p-0 shadow-none"

  React.useEffect(() => {
    const DOUBLE_TAP_WINDOW_MS = 1200
    const isEditableTarget = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) {
        return false
      }
      const tagName = target.tagName
      return (
        target.isContentEditable ||
        tagName === "INPUT" ||
        tagName === "TEXTAREA" ||
        tagName === "SELECT" ||
        target.getAttribute("role") === "textbox"
      )
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        isEditableTarget(event.target)
      ) {
        return
      }

      const key = event.key.toLowerCase()
      const now = Date.now()
      const pendingKey = pendingNavKeyRef.current
      const pendingAt = pendingNavAtRef.current
      const isWithinWindow =
        pendingKey !== null &&
        pendingAt !== null &&
        now - pendingAt <= DOUBLE_TAP_WINDOW_MS

      if (key !== "w" && key !== "r") {
        pendingNavKeyRef.current = null
        pendingNavAtRef.current = null
        return
      }

      if (!isWithinWindow || pendingKey !== key) {
        pendingNavKeyRef.current = key
        pendingNavAtRef.current = now
        return
      }

      pendingNavKeyRef.current = null
      pendingNavAtRef.current = null

      if (key === "w") {
        event.preventDefault()
        if (leafRoute !== "workflow") {
          router.push(builderPath)
        }
        return
      }

      if (key === "r") {
        event.preventDefault()
        if (leafRoute !== "executions") {
          router.push(executionsPath)
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [builderPath, executionsPath, leafRoute, router])

  return (
    <Tabs value={leafRoute}>
      <TabsList className="grid h-8 w-full grid-cols-2">
        <TabsTrigger
          className="w-full px-2 py-0 after:content-none"
          value="workflow"
          asChild
        >
          <Link href={builderPath} className="size-full text-xs" passHref>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="flex size-full items-center justify-center">
                  <WorkflowIcon className="mr-2 size-4" />
                  <span>Workflow</span>
                </span>
              </TooltipTrigger>
              <TooltipContent
                side="bottom"
                className={keyOnlyTooltipClassName}
                sideOffset={8}
              >
                <span className="inline-flex items-center gap-1">
                  <Kbd>W</Kbd>
                  <span className="inline-flex h-5 items-center rounded border bg-muted px-1.5 text-[10px] font-medium text-muted-foreground">
                    then
                  </span>
                  <Kbd>W</Kbd>
                </span>
              </TooltipContent>
            </Tooltip>
          </Link>
        </TabsTrigger>
        <TabsTrigger
          className="w-full px-2 py-0 after:content-none"
          value="executions"
          asChild
        >
          <Link href={executionsPath} className="size-full text-xs" passHref>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="flex size-full items-center justify-center">
                  <SquarePlay className="mr-2 size-4" />
                  <span>Runs</span>
                </span>
              </TooltipTrigger>
              <TooltipContent
                side="bottom"
                className={keyOnlyTooltipClassName}
                sideOffset={8}
              >
                <span className="inline-flex items-center gap-1">
                  <Kbd>R</Kbd>
                  <span className="inline-flex h-5 items-center rounded border bg-muted px-1.5 text-[10px] font-medium text-muted-foreground">
                    then
                  </span>
                  <Kbd>R</Kbd>
                </span>
              </TooltipContent>
            </Tooltip>
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

const publishFormSchema = z.object({
  message: z.string().optional(),
})
type TPublishForm = z.infer<typeof publishFormSchema>

function WorkflowManualTrigger({
  disabled = true,
  workflowId,
}: {
  disabled: boolean
  workflowId: string
}) {
  const { expandSidebarAndFocusEvents, setCurrentExecutionId } =
    useWorkflowBuilder()
  // Always use draft execution endpoint - runs the current draft workflow graph
  const { createDraftExecution, createDraftExecutionIsPending } =
    useCreateDraftWorkflowExecution(workflowId)
  const [open, setOpen] = React.useState(false)
  const [lastTriggerInput, setLastTriggerInput] = React.useState<string | null>(
    null
  )
  const [manualTriggerErrors, setManualTriggerErrors] = React.useState<
    ValidationResult[] | null
  >(null)
  const [isTriggering, setIsTriggering] = React.useState(false)
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      payload:
        lastTriggerInput ||
        JSON.stringify({ sampleWebhookParam: "sampleValue" }, null, 2),
    },
  })

  const runWorkflow = async ({ payload }: Partial<TWorkflowControlsForm>) => {
    if (disabled || createDraftExecutionIsPending) return
    setIsTriggering(true)
    setTimeout(() => setIsTriggering(false), 1000)
    setManualTriggerErrors(null)
    try {
      const result = await createDraftExecution({
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
        const tracecatError = error as TracecatApiError<{
          type?: string
          message?: string
          detail?: unknown
        }>
        console.error("Error details", tracecatError.body)
        const detail = tracecatError.body.detail
        let detailMessage: string | undefined
        if (typeof detail === "string") {
          detailMessage = detail
        } else if (
          detail &&
          typeof detail === "object" &&
          "message" in detail &&
          typeof (detail as { message?: unknown }).message === "string"
        ) {
          detailMessage = (detail as { message?: string }).message
        } else if (detail) {
          try {
            detailMessage = JSON.stringify(detail)
          } catch {
            detailMessage = undefined
          }
        }
        const details =
          Array.isArray(detail) && detail.every((d) => "msg" in (d as object))
            ? (detail as ValidationDetail[])
            : detailMessage
              ? [{ type: "api_error", msg: detailMessage }]
              : null
        // Convert API error to ValidationResult format for consistent display
        const validationError: DSLValidationResult = {
          type: "dsl",
          status: "error",
          msg: detailMessage || "Failed to start workflow",
          ref: null,
          detail: details,
        }
        setManualTriggerErrors([validationError])
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

  const executionPending = createDraftExecutionIsPending || isTriggering
  return (
    <Form {...form}>
      <ValidationErrorView
        side="bottom"
        validationErrors={manualTriggerErrors || []}
        noErrorTooltip={
          <span>
            {disabled
              ? "Cannot run workflow."
              : executionPending
                ? "Starting workflow execution..."
                : "Run the current draft workflow with trigger inputs."}
          </span>
        }
      >
        <div
          className={cn(
            "flex h-7 divide-x rounded-lg border border-input overflow-hidden",
            manualTriggerErrors
              ? "divide-white/30 dark:divide-black/30"
              : "divide-white/20 dark:divide-black/40"
          )}
        >
          {/* Main Button */}
          <Button
            type="button"
            variant={manualTriggerErrors ? "destructive" : "default"}
            className="h-full gap-2 rounded-r-none border-none px-3 py-0 text-xs"
            disabled={disabled || executionPending}
            onClick={() => runWorkflow({ payload: undefined })}
          >
            {executionPending ? (
              <Spinner className="size-3" segmentColor="currentColor" />
            ) : manualTriggerErrors ? (
              <AlertTriangleIcon className="size-3" />
            ) : (
              <PlayIcon className="size-3" />
            )}
            <span>Run</span>
          </Button>
          {/* Dropdown Button */}
          <Popover
            open={open && !disabled}
            onOpenChange={(newOpen) => !disabled && setOpen(newOpen)}
          >
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant={manualTriggerErrors ? "destructive" : "default"}
                className="h-full w-7 rounded-l-none border-none px-1 py-0 text-xs font-bold"
                disabled={disabled || executionPending}
              >
                <ChevronDownIcon className="size-3" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-fit max-w-xl p-3 sm:max-w-2xl">
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
                          <CodeEditor
                            value={field.value}
                            language="json"
                            onChange={field.onChange}
                            className="[&_.cm-editor]:!border [&_.cm-editor]:!border-input [&_.cm-editor]:!bg-background [&_.cm-editor]:rounded-md [&_.cm-scroller]:max-h-96 [&_.cm-scroller]:overflow-auto [&_.cm-scroller]:h-auto sm:[&_.cm-scroller]:max-h-[500px]"
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
                    className="group mt-2 flex h-7 items-center px-3 py-0 text-xs"
                  >
                    {executionPending ? (
                      <Spinner
                        className="mr-2 size-3.5"
                        segmentColor="currentColor"
                      />
                    ) : (
                      <PlayIcon className="mr-2 size-3.5" />
                    )}
                    <span>{executionPending ? "Starting..." : "Run"}</span>
                  </Button>
                </div>
              </form>
            </PopoverContent>
          </Popover>
        </div>
      </ValidationErrorView>
    </Form>
  )
}

function WorkflowSaveActions({
  workflow,
  validationErrors,
  onSave,
  onPublish,
}: {
  workflow: { version?: number | null }
  validationErrors: ValidationResult[] | null
  onSave: () => Promise<void>
  onPublish: (params: { message?: string }) => Promise<void>
}) {
  const { isFeatureEnabled } = useFeatureFlag()
  const [publishOpen, setPublishOpen] = React.useState(false)
  const [isPublishing, setIsPublishing] = React.useState(false)

  const publishForm = useForm<TPublishForm>({
    resolver: zodResolver(publishFormSchema),
    defaultValues: {
      message: "",
    },
  })

  const handlePublish = async (data: TPublishForm) => {
    setIsPublishing(true)
    try {
      await onPublish({ message: data.message || undefined })
    } finally {
      setIsPublishing(false)
      setPublishOpen(false)
      publishForm.reset()
    }
  }

  const isGitSyncEnabled = isFeatureEnabled("git-sync")

  return (
    <div className="flex items-center space-x-2">
      <div className="flex h-7 gap-px rounded-lg border border-input">
        {/* Main Publish Button */}
        <ValidationErrorView
          side="bottom"
          validationErrors={validationErrors || []}
          noErrorTooltip={
            <span>
              Publish workflow v{(workflow.version || 0) + 1} with your changes.
            </span>
          }
        >
          <Button
            variant="outline"
            onClick={onSave}
            className={cn(
              "h-full border-none px-3 py-0 text-xs text-muted-foreground",
              isGitSyncEnabled ? "rounded-r-none" : "rounded-lg",
              validationErrors &&
                "border-rose-400 text-rose-400 hover:bg-transparent hover:text-rose-500"
            )}
          >
            {validationErrors ? (
              <AlertTriangleIcon className="mr-2 size-3.5 fill-red-500 stroke-white" />
            ) : (
              <LayersPlusIcon className="mr-2 size-3.5" />
            )}
            Publish
          </Button>
        </ValidationErrorView>

        {/* Dropdown Button - Only show if git-sync is enabled */}
        {isGitSyncEnabled && (
          <DropdownMenu open={publishOpen} onOpenChange={setPublishOpen}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                className="h-full w-7 rounded-l-none border-none px-1 py-0 text-xs text-muted-foreground"
              >
                <ChevronDownIcon className="size-3" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-96 p-3">
              <Form {...publishForm}>
                <form
                  onSubmit={publishForm.handleSubmit(handlePublish)}
                  className="flex flex-col"
                >
                  <span className="mb-2 text-xs text-muted-foreground">
                    Commit workflow
                  </span>
                  <FormField
                    control={publishForm.control}
                    name="message"
                    render={({ field }) => (
                      <FormItem>
                        <FormControl>
                          <Input
                            {...field}
                            placeholder="Add a short description of your changes (optional)"
                            className="h-7 px-3 text-xs"
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <Button
                    type="submit"
                    disabled={isPublishing}
                    className="mt-2 flex h-7 w-full items-center justify-center gap-2 bg-primary px-3 py-0 text-xs text-white hover:bg-primary/80"
                  >
                    {isPublishing ? (
                      <>
                        <Spinner className="size-3" />
                        Publishing changes...
                      </>
                    ) : (
                      <>
                        <GitBranchIcon className="size-3" />
                        Publish changes
                      </>
                    )}
                  </Button>
                </form>
              </Form>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <Badge
        variant="secondary"
        className="h-7 text-xs font-normal text-muted-foreground hover:cursor-default"
      >
        {workflow.version ? `v${workflow.version}` : "Draft"}
      </Badge>
    </div>
  )
}

function BuilderNavOptions({
  workspaceId,
  workflowId,
}: {
  workspaceId: string
  workflowId: string
}) {
  const router = useRouter()
  const { appSettings } = useOrgAppSettings()
  const { deleteWorkflow } = useWorkflowManager()
  const enabledExport = appSettings?.app_workflow_export_enabled

  const handleDelete = async () => {
    await deleteWorkflow(workflowId)
    router.push(`/workspaces/${workspaceId}/workflows`)
  }

  return (
    <AlertDialog>
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
            draft={true}
            label="Export draft"
            icon={<DownloadIcon className="mr-2 size-3.5" />}
          />
          <ExportMenuItem
            enabledExport={enabledExport}
            format="yaml"
            workspaceId={workspaceId}
            workflowId={workflowId}
            draft={false}
            label="Export saved"
            icon={<DownloadIcon className="mr-2 size-3.5" />}
          />
          <DropdownMenuItem
            onClick={() =>
              copyToClipboard({
                value: workflowId,
                message: "Copied workflow ID to clipboard",
              })
            }
          >
            <CopyIcon className="mr-2 size-3.5" />
            Copy workflow ID
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <AlertDialogTrigger asChild>
            <DropdownMenuItem className="text-destructive focus:text-destructive">
              <Trash2Icon className="mr-2 size-3.5" />
              Delete workflow
            </DropdownMenuItem>
          </AlertDialogTrigger>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete workflow</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this workflow? This action cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={handleDelete}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
