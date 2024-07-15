"use client"

import React, { useCallback } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { ApiError, workflowExecutionsCreateWorkflowExecution } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"
import { Editor } from "@monaco-editor/react"
import {
  GitPullRequestCreateArrowIcon,
  PlayIcon,
  ShieldAlertIcon,
  SquarePlay,
  WorkflowIcon,
} from "lucide-react"
import { editor } from "monaco-editor"
import { useForm } from "react-hook-form"
import { z } from "zod"

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
import { CenteredSpinner } from "@/components/loading/spinner"

export default function WorkflowNav() {
  const { workflow, isLoading, isOnline, setIsOnline, commit } = useWorkflow()

  const handleCommit = async () => {
    console.log("Committing changes...")
    await commit()
  }

  if (!workflow || isLoading) {
    return null
  }

  return (
    <div className="flex w-full items-center space-x-8">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="/workflows">Workflows</BreadcrumbLink>
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
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              className="group flex h-7 items-center px-3 py-0 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white"
            >
              <PlayIcon className="mr-2 size-3 fill-emerald-500 stroke-emerald-500 group-hover:fill-white group-hover:stroke-white" />
              <span>Run</span>
            </Button>
          </PopoverTrigger>
          <PopoverContent className="p-3">
            <WorkflowExecutionControls workflowId={workflow.id} />
          </PopoverContent>
        </Popover>

        {/* Commit button */}
        <div className="flex items-center space-x-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                onClick={handleCommit}
                className="h-7 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white"
              >
                <GitPullRequestCreateArrowIcon className="mr-2 size-4" />
                Commit
              </Button>
            </TooltipTrigger>
            <TooltipContent
              side="bottom"
              className="max-w-48 border bg-background text-xs text-muted-foreground shadow-lg"
            >
              Create workflow definition v{(workflow.version || 0) + 1} with
              your changes.
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
      </div>
    </div>
  )
}

function TabSwitcher({ workflowId }: { workflowId: string }) {
  const pathname = usePathname()
  let leafRoute: string = "workflow"
  if (pathname.endsWith("cases")) {
    leafRoute = "cases"
  } else if (pathname.endsWith("executions")) {
    leafRoute = "executions"
  }

  return (
    <Tabs value={leafRoute}>
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger className="w-full px-4 py-0" value="workflow" asChild>
          <Link
            href={`/workflows/${workflowId}`}
            className="size-full text-sm"
            passHref
          >
            <WorkflowIcon className="mr-2 size-4" />
            <span>Workflow</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full px-4 py-0" value="cases" asChild>
          <Link
            href={`/workflows/${workflowId}/cases`}
            className="size-full text-sm"
            passHref
          >
            <ShieldAlertIcon className="mr-2 size-4" />
            <span>Cases</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full px-4 py-0" value="executions" asChild>
          <Link
            href={`/workflows/${workflowId}/executions`}
            className="size-full text-sm"
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
  payload: z.record(z.string(), z.unknown()).optional(),
})
type TWorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

function WorkflowExecutionControls({ workflowId }: { workflowId: string }) {
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {},
  })

  const handleSubmit = useCallback(async () => {
    // Make the API call to start the workflow
    const values = form.getValues()
    try {
      const response = await workflowExecutionsCreateWorkflowExecution({
        requestBody: {
          workflow_id: workflowId,
          inputs: values.payload,
        },
      })
      console.log("Workflow started", response)
      toast({
        title: `Workflow Run Started`,
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
                  <div className="h-full w-full border">
                    {/* The json contains the view into the data */}
                    <div className="h-36">
                      <Editor
                        height="100%"
                        theme="vs-light"
                        defaultLanguage="json"
                        defaultValue={"{}"}
                        onChange={(
                          value: string | undefined,
                          ev: editor.IModelContentChangedEvent
                        ) => {
                          const newValue = value ? JSON.parse(value) : {}
                          field.onChange(newValue)
                        }}
                        loading={<CenteredSpinner />}
                        options={{
                          tabSize: 2,
                          minimap: { enabled: false },
                          scrollbar: {
                            verticalScrollbarSize: 5,
                            horizontalScrollbarSize: 5,
                          },
                          folding: false,
                          glyphMargin: true,
                          lineNumbersMinChars: 2,
                        }}
                      />
                    </div>
                  </div>
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
