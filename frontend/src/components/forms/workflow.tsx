"use client"

import { useEffect, useState } from "react"
import * as React from "react"
import { useSession } from "@/providers/session"
import { useWorkflowMetadata } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { useRouter } from "next/navigation"
import { CaretSortIcon, HamburgerMenuIcon } from "@radix-ui/react-icons"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@radix-ui/react-popover"
import { ScrollArea } from "@radix-ui/react-scroll-area"
import { useMutation, useQuery } from "@tanstack/react-query"
import {
  CheckIcon,
  CircleCheck,
  CircleIcon,
  CircleX,
  Loader2,
  Save,
  Send,
} from "lucide-react"
import { useForm } from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"
import { z } from "zod"

import {
  Action,
  ActionRun,
  RunStatus,
  Workflow,
  WorkflowRun,
} from "@/types/schemas"
import { stringToJSONSchema } from "@/types/validators"
import {
  deleteWorkflow,
  fetchWorkflowRun,
  fetchWorkflowRuns,
  triggerWorkflow,
  updateWorkflow,
} from "@/lib/flow"
import { cn, getActionKey, parseActionRunId } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
} from "@/components/ui/command"
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
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import DecoratedHeader from "@/components/decorated-header"
import { CenteredSpinner } from "@/components/loading/spinner"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"

const workflowFormSchema = z.object({
  title: z.string(),
  description: z.string(),
})

type WorkflowForm = z.infer<typeof workflowFormSchema>

interface WorkflowFormProps {
  workflow: Workflow
  isOnline: boolean
}

export function WorkflowForm({
  workflow,
  isOnline,
}: WorkflowFormProps): React.JSX.Element {
  const {
    id: workflowId,
    title: workflowTitle,
    description: workflowDescription,
  } = workflow
  const session = useSession()
  const form = useForm<WorkflowForm>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
      title: workflowTitle || "",
      description: workflowDescription || "",
    },
  })

  function useUpdateWorkflow(workflowId: string) {
    const mutation = useMutation({
      mutationFn: (values: WorkflowForm) =>
        updateWorkflow(session, workflowId, values),
      onSuccess: (data, variables, context) => {
        console.log("Workflow update successful", data)
        toast({
          title: "Saved workflow",
          description: "Workflow updated successfully.",
        })
      },
      onError: (error, variables, context) => {
        console.error("Failed to update workflow:", error)
        toast({
          title: "Error updating workflow",
          description: "Could not update workflow. Please try again.",
        })
      },
    })

    return mutation
  }

  const { mutate } = useUpdateWorkflow(workflowId)
  function onSubmit(values: WorkflowForm) {
    mutate(values)
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-4">
          <div className="space-y-3">
            <div className="flex justify-between">
              <h4 className="text-sm font-medium">Workflow Status</h4>
              <WorkflowSettings workflow={workflow} />
            </div>
            <div className="flex justify-between">
              <Badge
                variant="outline"
                className={cn(
                  "px-4 py-1 capitalize",
                  isOnline ? "bg-green-600/10" : "bg-gray-100"
                )}
              >
                <CircleIcon
                  className={cn(
                    "mr-2 h-3 w-3",
                    isOnline
                      ? "fill-green-600 text-green-600"
                      : "fill-gray-400 text-gray-400"
                  )}
                />
                <span
                  className={cn(isOnline ? "text-green-600" : "text-gray-600")}
                >
                  {isOnline ? "online" : "offline"}
                </span>
              </Badge>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button type="submit" size="icon">
                    <Save className="h-4 w-4" />
                    <span className="sr-only">Save</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Save</TooltipContent>
              </Tooltip>
            </div>
          </div>
          <Separator />
          <div className="space-y-4">
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-xs">Name</FormLabel>
                  <FormControl>
                    <Input
                      className="text-xs"
                      placeholder="Add workflow name..."
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
          </div>
        </div>
      </form>
    </Form>
  )
}

function WorkflowSettings({ workflow }: { workflow: Workflow }) {
  const session = useSession()
  const router = useRouter()
  const handleDeleteWorkflow = async () => {
    console.log("Delete workflow")
    await deleteWorkflow(session, workflow.id)
    router.push("/workflows")
    toast({
      title: "Workflow deleted",
      description: `The workflow "${workflow.title}" has been deleted.`,
    })
    router.refresh()
  }
  return (
    <div>
      <Dialog>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="flex h-8 w-8 p-0 data-[state=open]:bg-muted"
            >
              <HamburgerMenuIcon className="h-4 w-4" />
              <span className="sr-only">Open menu</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[160px]">
            <DropdownMenuItem disabled>Make a copy</DropdownMenuItem>
            <DropdownMenuItem disabled>Favorite</DropdownMenuItem>
            <DialogTrigger asChild>
              <DropdownMenuItem className="text-red-600">
                Delete
              </DropdownMenuItem>
            </DialogTrigger>
          </DropdownMenuContent>
        </DropdownMenu>

        <DialogContent>
          <DialogHeader className="space-y-4">
            <DialogTitle>
              Are you sure you want to delete this workflow?
            </DialogTitle>
            <DialogDescription className="flex items-center text-sm text-foreground">
              You are about to delete the workflow
              <b className="ml-1">{workflow.title}</b>. Proceed?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button
                className="ml-auto space-x-2 border-0 font-bold text-white"
                variant="destructive"
                onClick={handleDeleteWorkflow}
              >
                Delete Workflow
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

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
type WorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

export function WorkflowControlsForm({
  workflow,
}: {
  workflow: Workflow
}): React.JSX.Element {
  const session = useSession()
  const [confirmationIsOpen, setConfirmationIsOpen] = useState(false)
  const form = useForm<WorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      payload: "",
      actionKey: undefined,
    },
  })
  const [selectedAction, setSelectedAction] = useState<Action | null>(null)

  const onSubmit = async (values: WorkflowControlsForm) => {
    // Make the API call to start the workflow
    console.log(values)
    if (!values.actionKey) {
      console.error("No action key provided")
      toast({
        title: "No action key provided",
        description: "Please select an action to start the workflow.",
      })
      return
    }

    try {
      const data = JSON.parse(values.payload)
      await triggerWorkflow(session, workflow.id, values.actionKey, data)
    } catch (e) {
      console.error("Invalid JSON payload")
      toast({
        title: "Invalid JSON payload",
        description: "Please provide a valid JSON payload.",
      })
    }
  }
  useEffect(() => {
    if (selectedAction) {
      console.log("Selected action", selectedAction)
      form.setValue("actionKey", getActionKey(selectedAction))
    }
  }, [selectedAction])
  return (
    <Form {...form}>
      <form className="space-y-4">
        <div className="space-y-3">
          <h4 className="text-sm font-medium">Controls</h4>
        </div>
        <Separator />

        <AlertDialog open={confirmationIsOpen}>
          <FormField
            control={form.control}
            name="payload"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-xs">Send Payload</FormLabel>
                <div className="flex w-full items-center space-x-2">
                  <EntrypointSelector
                    selectedAction={selectedAction}
                    setSelectedaction={setSelectedAction}
                  />
                  <AlertDialogTrigger asChild>
                    <Button
                      type="button"
                      onClick={async () => {
                        const validated = await form.trigger()
                        if (!form.getValues("actionKey")) {
                          console.error("No action key provided")
                          toast({
                            title: "No action provided",
                            description:
                              "Please select an action to start the workflow.",
                          })
                          return
                        }
                        setConfirmationIsOpen(validated)
                      }}
                    >
                      <div className="flex items-center space-x-2">
                        <Send className="h-4 w-4" />
                        <span>Send</span>
                      </div>
                    </Button>
                  </AlertDialogTrigger>
                </div>
                <FormControl>
                  <pre>
                    <Textarea
                      {...field}
                      className="min-h-48 text-xs"
                      value={form.watch("payload", "")}
                      placeholder="Select an action as the workflow entrypoint, and define a JSON payload that will be sent to it."
                    />
                  </pre>
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Start workflow confirmation</AlertDialogTitle>
              <AlertDialogDescription className="flex flex-col">
                <span>
                  You are about to start the workflow with the selected action:
                </span>
                <span className="font-bold">{selectedAction?.title}</span>
                <span>This is the payload that will be sent:</span>
              </AlertDialogDescription>
              <SyntaxHighlighter
                language="json"
                style={atomOneDark}
                wrapLines
                customStyle={{
                  width: "100%",
                  maxWidth: "100%",
                  overflowX: "auto",
                }}
                codeTagProps={{
                  className:
                    "text-xs text-background rounded-lg max-w-full overflow-auto",
                }}
                {...{
                  className:
                    "rounded-lg p-4 overflow-auto max-w-full w-full no-scrollbar",
                }}
              >
                {form.watch("payload")}
              </SyntaxHighlighter>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel onClick={() => setConfirmationIsOpen(false)}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction onClick={() => form.handleSubmit(onSubmit)()}>
                <div className="flex items-center space-x-2">
                  <Send className="h-4 w-4" />
                  <span>Confirm</span>
                </div>
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </form>
    </Form>
  )
}

export default function EntrypointSelector({
  className,
  selectedAction,
  setSelectedaction,
}: {
  className?: string
  selectedAction: Action | null
  setSelectedaction: React.Dispatch<React.SetStateAction<Action | null>>
}) {
  const { workflow } = useWorkflowMetadata()
  const [actions, setActions] = useState<Action[]>([])
  const [open, setOpen] = useState(false)

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
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-label="Load a webhook ..."
          aria-expanded={open}
          className="w-full flex-1 justify-between text-xs font-normal"
        >
          {selectedAction?.title ?? "Select a webhook..."}
          <CaretSortIcon className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className={cn(
          "w-[300px] rounded-md border-[1px] border-zinc-200 shadow-lg",
          className
        )}
        align="start"
      >
        <Command>
          <CommandInput className="text-xs" placeholder="Search webhooks..." />
          <CommandEmpty>No actions found.</CommandEmpty>
          <CommandGroup heading="Webhooks">
            {actions.map((action) => (
              <CommandItem
                key={action.id}
                className="text-xs"
                onSelect={() => {
                  setSelectedaction(action)
                  setOpen(false)
                }}
              >
                {action.title}
                <CheckIcon
                  className={cn(
                    "ml-auto h-4 w-4",
                    selectedAction === action ? "opacity-100" : "opacity-0"
                  )}
                />
              </CommandItem>
            ))}
          </CommandGroup>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

export function WorkflowRunsView({
  workflowId,
  className,
}: {
  workflowId: string
  className?: string
}) {
  const session = useSession()

  const {
    data: workflowRuns,
    isLoading,
    error,
  } = useQuery<WorkflowRun[], Error>({
    queryKey: ["workflow", workflowId, "runs"],
    queryFn: async ({ queryKey }) => {
      const [_workflow, workflowId, _run] = queryKey as [
        string?,
        string?,
        string?,
      ]
      if (!workflowId) {
        throw new Error("No workflow ID provided")
      }
      const data = await fetchWorkflowRuns(session, workflowId)
      return data
    },
  })
  return (
    <div className="space-y-3">
      <h1 className="text-xs font-medium">Past Runs</h1>
      <ScrollArea
        className={cn(
          "h-full max-h-[400px] overflow-y-auto rounded-md border p-4",
          className
        )}
      >
        {isLoading ? (
          <CenteredSpinner />
        ) : error ? (
          <AlertNotification
            level="error"
            message="Error loading workflow runs"
          />
        ) : workflowRuns && workflowRuns.length > 0 ? (
          <Accordion type="single" collapsible className="w-full">
            {workflowRuns
              ?.sort((a, b) => b.created_at.getTime() - a.created_at.getTime())
              .map((props, index) => (
                <WorkflowRunItem
                  className="my-2 w-full"
                  key={index}
                  {...props}
                />
              ))}
          </Accordion>
        ) : (
          <NoContent className="my-8" message="No runs available" />
        )}
      </ScrollArea>
    </div>
  )
}

function WorkflowRunItem({
  className,
  status,
  id: workflowRunId,
  workflow_id: workflowId,
  created_at,
  updated_at,
  ...props
}: React.PropsWithoutRef<WorkflowRun> & React.HTMLAttributes<HTMLDivElement>) {
  const session = useSession()
  const [open, setOpen] = useState(false)
  const [actionRuns, setActionRuns] = useState<ActionRun[]>([])
  const handleClick = () => setOpen(!open)

  useEffect(() => {
    if (open) {
      fetchWorkflowRun(session, workflowId, workflowRunId).then((res) =>
        setActionRuns(res.action_runs)
      )
    }
  }, [open])
  return (
    <AccordionItem value={created_at.toString()}>
      <AccordionTrigger onClick={handleClick}>
        <div className="mr-2 flex w-full items-center justify-between">
          <DecoratedHeader
            size="sm"
            node={`${created_at.toLocaleDateString()}, ${created_at.toLocaleTimeString()}`}
            icon={status === "success" ? CircleCheck : CircleX}
            iconProps={{
              className: cn(
                "stroke-2",
                status === "success"
                  ? "fill-green-500/50 stroke-green-700"
                  : "fill-red-500/50 stroke-red-700"
              ),
            }}
            className="font-medium capitalize"
          />
          <span className="text-xs text-muted-foreground">
            Updated: {updated_at.toLocaleTimeString()}
          </span>
        </div>
      </AccordionTrigger>
      <AccordionContent className="space-y-2 pl-2">
        <Separator className="mb-4" />
        {actionRuns.map(({ id, created_at, updated_at, status }, index) => {
          const { icon, style } = getStyle(status)
          return (
            <div
              key={index}
              className="mr-2 flex w-full items-center justify-between"
            >
              <DecoratedHeader
                size="sm"
                className="font-medium"
                node={
                  <span className="flex items-center text-xs">
                    <span>
                      {created_at.toLocaleDateString()}{" "}
                      {created_at.toLocaleTimeString()}
                    </span>
                    <span className="ml-4 font-normal">
                      {parseActionRunId(id)}
                    </span>
                  </span>
                }
                icon={icon}
                iconProps={{
                  className: cn(
                    "stroke-2",
                    style,
                    (status === "running" || status === "pending") &&
                      "animate-spin fill-background"
                  ),
                }}
              />
              <span className="text-xs text-muted-foreground">
                Updated: {updated_at.toLocaleTimeString()}
              </span>
            </div>
          )
        })}
      </AccordionContent>
    </AccordionItem>
  )
}

function getStyle(status: RunStatus) {
  switch (status) {
    case "success":
      return { icon: CircleCheck, style: "fill-green-500/50 stroke-green-700" }
    case "failure":
      return { icon: CircleX, style: "fill-red-500/50 stroke-red-700" }
    case "running":
      return {
        icon: Loader2,
        style: "stroke-yellow-500 animate-spin",
      }
    case "pending":
      return { icon: Loader2, style: "stroke-yellow-500 animate-spin" }
    case "canceled":
      return { icon: CircleX, style: "fill-red-500/50 stroke-red-700" }
    default:
      throw new Error("Invalid status")
  }
}
