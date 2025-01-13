"use client"

import React from "react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import {
  ApiError,
  RegistryActionValidateResponse,
  workflowExecutionsCreateWorkflowExecution,
} from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  DownloadIcon,
  MoreHorizontal,
  PlayIcon,
  SaveIcon,
  SquarePlay,
  Trash2Icon,
  WorkflowIcon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import YAML from "yaml"
import { z } from "zod"

import { TracecatApiError } from "@/lib/errors"
import { exportWorkflow, handleExportError } from "@/lib/export"
import { useWorkflowManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
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
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
import { toast } from "@/components/ui/use-toast"
import { DynamicCustomEditor } from "@/components/editor/dynamic"

export function WorkbenchNav() {
  const {
    workflow,
    isLoading: workflowLoading,
    isOnline,
    setIsOnline,
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

            <TooltipContent
              side="bottom"
              className="w-fit border bg-background p-0 text-xs text-muted-foreground shadow-lg"
            >
              {validationErrors ? (
                <div className="space-y-2 rounded-md border border-rose-400 bg-rose-100 p-2 font-mono tracking-tighter">
                  <span className="text-xs font-bold text-rose-500">
                    Validation Errors
                  </span>
                  <div className="mt-1 space-y-1">
                    {validationErrors.map((error, index) => (
                      <div className="space-y-2">
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
                <div className="p-2">
                  <span>
                    Save workflow v{(workflow.version || 0) + 1} with your
                    changes.
                  </span>
                </div>
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

        {/* Workflow status */}
        <Tooltip>
          <AlertDialog>
            <TooltipTrigger asChild>
              <AlertDialogTrigger asChild>
                <Button
                  variant="outline"
                  className={cn(
                    "h-7 text-xs font-bold",
                    isOnline
                      ? "text-rose-400 hover:text-rose-600"
                      : "bg-emerald-500 text-white hover:bg-emerald-500/80 hover:text-white"
                  )}
                >
                  {isOnline ? "Disable Workflow" : "Enable Workflow"}
                </Button>
              </AlertDialogTrigger>
            </TooltipTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {isOnline ? "Disable Workflow?" : "Enable Workflow?"}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {isOnline
                    ? "Are you sure you want to disable the workflow? This will pause all schedules and block webhook events."
                    : "Are you sure you want to enable the workflow? This will resume all schedules and allow webhook events."}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={() => setIsOnline(!isOnline)}>
                  Confirm
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <TooltipContent
            side="bottom"
            className="w-72 border bg-background text-xs text-muted-foreground shadow-lg"
          >
            {isOnline
              ? "Disable the workflow to pause all schedules and block webhook events."
              : "Enable the workflow to resume all schedules and allow webhook events."}
          </TooltipContent>
        </Tooltip>

        {/* Workflow options */}
        <WorkbenchNavOptions
          workspaceId={workspaceId}
          workflowId={workflow.id}
        />
      </div>
    </div>
  )
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
  const formattedDetail = detail ? YAML.stringify(detail, null, 2) : null

  return (
    <pre
      className={cn("overflow-auto whitespace-pre-wrap text-wrap", className)}
    >
      {formattedMessage}
      {formattedDetail && (
        <React.Fragment>
          <br />
          {formattedDetail}
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
  const [open, setOpen] = React.useState(false)
  const { workspaceId } = useWorkspace()
  const [lastTriggerInput, setLastTriggerInput] = React.useState<string | null>(
    null
  )
  const [manualTriggerErrors, setManualTriggerErrors] = React.useState<Record<
    string,
    string
  > | null>(null)
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      payload: lastTriggerInput || '{"sampleWebhookParam": "sampleValue"}',
    },
  })

  const handleSubmit = async (values: TWorkflowControlsForm) => {
    // Make the API call to start the workflow
    const { payload } = values
    setLastTriggerInput(payload)
    setManualTriggerErrors(null)
    try {
      const response = await workflowExecutionsCreateWorkflowExecution({
        workspaceId,
        requestBody: {
          workflow_id: workflowId,
          inputs: payload ? JSON.parse(payload) : undefined,
        },
      })
      console.log("Workflow started", response)
      toast({
        title: `Workflow run started`,
        description: `${response.wf_exec_id} ${response.message}`,
      })
    } catch (error) {
      if (error instanceof ApiError) {
        const tracecatError = error as TracecatApiError
        console.error("Error details", tracecatError.body.detail)
        switch (tracecatError.status) {
          case 400:
            toast({
              title: "Invalid workflow trigger inputs",
              description: "Please hover over the run button for details.",
              variant: "destructive",
            })
            setManualTriggerErrors(
              tracecatError.body.detail as Record<string, string>
            )
            break
          default:
            console.error("Unexpected error starting workflow", error)
            toast({
              title: "Unexpected error starting workflow",
              description: "Please check the run logs for more information",
              variant: "destructive",
            })
        }
      } else {
        console.error("Unexpected error starting workflow", error)
        toast({
          title: "Unexpected error starting workflow",
          description: "Please check the run logs for more information",
          variant: "destructive",
        })
      }
    } finally {
      setOpen(false)
    }
  }

  return (
    <Form {...form}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span>
            <Popover
              open={open && !disabled}
              onOpenChange={(newOpen) => !disabled && setOpen(newOpen)}
            >
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  className={cn(
                    "group flex h-7 items-center px-3 py-0 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white disabled:pointer-events-none",
                    manualTriggerErrors &&
                      "border-rose-400 text-rose-400 hover:bg-transparent hover:text-rose-500"
                  )}
                  disabled={disabled}
                  onClick={() => !disabled && setOpen(true)}
                >
                  {manualTriggerErrors ? (
                    <AlertTriangleIcon className="mr-2 size-4 fill-red-500 stroke-white" />
                  ) : (
                    <PlayIcon className="mr-2 size-3 fill-emerald-500 stroke-emerald-500 group-hover:fill-white group-hover:stroke-white" />
                  )}
                  <span>Run</span>
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-fit p-3">
                <form onSubmit={form.handleSubmit(handleSubmit)}>
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
                      className="group mt-2 flex h-7 items-center bg-emerald-500 px-3 py-0 text-xs text-white hover:bg-emerald-500/80 hover:text-white"
                    >
                      <PlayIcon className="mr-2 size-3 fill-white stroke-white" />
                      <span>Run</span>
                    </Button>
                  </div>
                </form>
              </PopoverContent>
            </Popover>
          </span>
        </TooltipTrigger>
        <TooltipContent
          side="bottom"
          className={cn(
            "w-96 border bg-background text-xs text-muted-foreground shadow-lg",
            manualTriggerErrors && "p-0"
          )}
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
          ) : (
            "Run the workflow manually without a webhook. Click to configure inputs."
          )}
        </TooltipContent>
      </Tooltip>
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
  const router = useRouter()
  const { deleteWorkflow } = useWorkflowManager()

  const handleDeleteWorkflow = async () => {
    console.log("Delete workflow")
    await deleteWorkflow(workflowId)
    router.push(`/workspaces/${workspaceId}`)
  }

  return (
    <>
      <Dialog>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon">
              <MoreHorizontal className="size-4" />
              <span className="sr-only">More</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              className="text-xs text-foreground/70"
              onClick={async () => {
                try {
                  await exportWorkflow({
                    workspaceId,
                    workflowId,
                    format: "json",
                  })
                } catch (error) {
                  console.error(
                    "Failed to download JSON workflow definition:",
                    error
                  )
                  toast(handleExportError(error as Error))
                }
              }}
            >
              <DownloadIcon className="mr-2 size-4" />
              <span>Export as JSON</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-xs text-foreground/70"
              onClick={async () => {
                try {
                  await exportWorkflow({
                    workspaceId,
                    workflowId,
                    format: "yaml",
                  })
                } catch (error) {
                  console.error(
                    "Failed to download YAML workflow definition:",
                    error
                  )
                  toast(handleExportError(error as Error))
                }
              }}
            >
              <DownloadIcon className="mr-2 size-4" />
              <span>Export as YAML</span>
            </DropdownMenuItem>
            <DialogTrigger asChild>
              <DropdownMenuItem className="text-xs text-red-600">
                <Trash2Icon className="mr-2 size-4" />
                <span>Delete</span>
              </DropdownMenuItem>
            </DialogTrigger>
          </DropdownMenuContent>
        </DropdownMenu>

        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete workflow</DialogTitle>
            <DialogClose />
          </DialogHeader>
          <DialogDescription>
            Are you sure you want to permanently delete this workflow?
          </DialogDescription>
          <DialogFooter>
            <Button variant="outline">Cancel</Button>
            <Button
              onClick={handleDeleteWorkflow}
              variant="destructive"
              className="mr-2"
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
