"use client"

import React, { useCallback } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  ApiError,
  UDFArgsValidationResponse,
  workflowExecutionsCreateWorkflowExecution,
} from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  DownloadIcon,
  GitPullRequestCreateArrowIcon,
  MoreHorizontal,
  PlayIcon,
  ShieldAlertIcon,
  SquarePlay,
  WorkflowIcon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { exportWorkflowJson } from "@/lib/export"
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CustomEditor } from "@/components/editor"

export function WorkbenchNav() {
  const {
    workflow,
    isLoading: workflowLoading,
    isOnline,
    setIsOnline,
    commitWorkflow,
  } = useWorkflow()

  const { workspaceId, workspace, workspaceLoading } = useWorkspace()

  const [commitErrors, setCommitErrors] = React.useState<
    UDFArgsValidationResponse[] | null
  >(null)

  const handleCommit = async () => {
    console.log("Committing changes...")
    const response = await commitWorkflow()
    const { status, errors } = response
    if (status === "failure") {
      setCommitErrors(errors || null)
    } else {
      setCommitErrors(null)
    }
  }

  if (!workflow || workflowLoading || !workspace || workspaceLoading) {
    return null
  }

  const manualTriggerDisabled = workflow.version === null
  const workflowsPath = `/workspaces/${workspaceId}/workflows`
  return (
    <div className="flex w-full items-center space-x-8">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href={workflowsPath}>
              {workspace.name}
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator className="font-semibold">
            {"/"}
          </BreadcrumbSeparator>
          <BreadcrumbItem>{workflow.title}</BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <TabSwitcher workflowId={workflow.id} />

      <div className="flex flex-1 items-center justify-end space-x-6">
        {/* Workflow manual trigger */}
        <Popover>
          <Tooltip>
            <PopoverTrigger asChild>
              <TooltipTrigger asChild>
                <span>
                  <Button
                    type="button"
                    variant="outline"
                    className="group flex h-7 items-center px-3 py-0 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white"
                    disabled={manualTriggerDisabled}
                  >
                    <PlayIcon className="mr-2 size-3 fill-emerald-500 stroke-emerald-500 group-hover:fill-white group-hover:stroke-white" />
                    <span>Run</span>
                  </Button>
                </span>
              </TooltipTrigger>
            </PopoverTrigger>
            <TooltipContent
              side="bottom"
              className="max-w-48 border bg-background text-xs text-muted-foreground shadow-lg"
            >
              {manualTriggerDisabled
                ? "Please commit changes to enable manual trigger."
                : "Run the workflow manually without a webhook. Click to configure inputs."}
            </TooltipContent>
            <PopoverContent className="p-3">
              <WorkflowExecutionControls workflowId={workflow.id} />
            </PopoverContent>
          </Tooltip>
        </Popover>

        {/* Commit button */}
        <div className="flex items-center space-x-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                onClick={handleCommit}
                className={cn(
                  "h-7 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white",
                  commitErrors &&
                    "border-rose-400 text-rose-400 hover:bg-transparent hover:text-rose-500"
                )}
              >
                {commitErrors ? (
                  <AlertTriangleIcon className="mr-2 size-4 fill-red-500 stroke-white" />
                ) : (
                  <GitPullRequestCreateArrowIcon className="mr-2 size-4" />
                )}
                Commit
              </Button>
            </TooltipTrigger>

            <TooltipContent
              side="bottom"
              className="max-w-72 space-y-2 border bg-background p-0 text-xs text-muted-foreground shadow-lg"
            >
              {commitErrors ? (
                <div className="rounded-md border border-rose-400 bg-rose-100 p-2 font-mono tracking-tighter">
                  <span className="text-xs font-bold text-rose-500">
                    Validation errors:
                  </span>
                  <ul className="mt-1 space-y-1">
                    {commitErrors.map((error, index) => (
                      <li key={index} className="text-xs">
                        {error.message}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="p-2">
                  <span>
                    Create workflow definition v{(workflow.version || 0) + 1}{" "}
                    with your changes.
                  </span>
                </div>
              )}
            </TooltipContent>
          </Tooltip>

          <Badge
            variant="secondary"
            className="h-7 text-xs font-normal text-muted-foreground hover:cursor-default"
          >
            {workflow.version ? `v${workflow.version}` : "Not Committed"}
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
                    ? "Are you sure you want to disable the workflow? This will stop new executions and event processing."
                    : "Are you sure you want to enable the workflow? This will start new executions and event processing."}
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
            className="max-w-48 border bg-background text-xs text-muted-foreground shadow-lg"
          >
            {isOnline ? "Disable" : "Enable"} the workflow to{" "}
            {isOnline ? "stop" : "start"} new executions and receive events.
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

function TabSwitcher({ workflowId }: { workflowId: string }) {
  const pathname = usePathname()
  const { workspaceId } = useWorkspace()
  let leafRoute: string = "workflow"
  if (pathname.endsWith("cases")) {
    leafRoute = "cases"
  } else if (pathname.endsWith("executions")) {
    leafRoute = "executions"
  }

  const workbenchPath = `/workspaces/${workspaceId}/workflows/${workflowId}`

  return (
    <Tabs value={leafRoute}>
      <TabsList className="grid h-8 w-full grid-cols-3">
        <TabsTrigger className="w-full px-2 py-0" value="workflow" asChild>
          <Link href={workbenchPath} className="size-full text-xs" passHref>
            <WorkflowIcon className="mr-2 size-4" />
            <span>Workflow</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full px-2 py-0" value="cases" asChild>
          <Link
            href={workbenchPath + "/cases"}
            className="size-full text-xs"
            passHref
          >
            <ShieldAlertIcon className="mr-2 size-4" />
            <span>Cases</span>
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
  payload: z.string().refine((val) => {
    try {
      JSON.parse(val)
      return true
    } catch {
      return false
    }
  }, "Invalid JSON format"),
})
type TWorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

function WorkflowExecutionControls({ workflowId }: { workflowId: string }) {
  const { workspaceId } = useWorkspace()
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: { payload: '{"example": "value"}' },
  })

  const handleSubmit = useCallback(async () => {
    // Make the API call to start the workflow
    const { payload } = form.getValues()
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
  }, [workflowId, form])

  return (
    <Form {...form}>
      <form>
        <div className="flex flex-col space-y-2">
          <span className="text-xs text-muted-foreground">
            Edit the JSON payload below.
          </span>
          <FormField
            control={form.control}
            name="payload"
            render={({ field }) => (
              <FormItem>
                <FormControl>
                  <CustomEditor
                    className="size-full h-36"
                    defaultLanguage="yaml"
                    value={field.value}
                    onChange={field.onChange}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <Button
            type="button"
            variant="default"
            onClick={handleSubmit}
            className="group flex h-7 items-center bg-emerald-500 px-3 py-0 text-xs text-white hover:bg-emerald-500/80 hover:text-white"
          >
            <PlayIcon className="mr-2 size-3 fill-white stroke-white" />
            <span>Run</span>
          </Button>
        </div>
      </form>
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
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon">
          <MoreHorizontal className="size-4" />
          <span className="sr-only">More</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          onClick={async () => {
            try {
              await exportWorkflowJson({
                workspaceId,
                workflowId,
                format: "json",
              })
            } catch (error) {
              console.error("Failed to download workflow definition:", error)
              toast({
                title: "Error exporting workflow",
                description: "Could not export workflow. Please try again.",
              })
            }
          }}
        >
          <DownloadIcon className="mr-2 size-4 text-foreground/70" />
          <span className="text-xs text-foreground/70">Export as JSON</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
