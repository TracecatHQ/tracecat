"use client"

import { useEffect, useState } from "react"
import * as React from "react"
import { useSession } from "@/providers/session"
import { useWorkflowMetadata } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"
import { CaretSortIcon } from "@radix-ui/react-icons"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@radix-ui/react-popover"
import { useMutation } from "@tanstack/react-query"
import { CheckIcon, CircleIcon, Save, Send } from "lucide-react"
import { useForm } from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"
import { z } from "zod"

import { Action, Workflow } from "@/types/schemas"
import { triggerWorkflow, updateWorkflow } from "@/lib/flow"
import { cn, getActionKey } from "@/lib/utils"
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
        <div className="space-y-4 p-4">
          <div className="space-y-3">
            <h4 className="text-sm font-medium">Workflow Status</h4>
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

const workflowControlsFormSchema = z.object({
  payload: z.string(), // json
  actionKey: z.string(),
})
type WorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

export function WorkflowControlsForm({
  workflow,
}: {
  workflow: Workflow
}): React.JSX.Element {
  const session = useSession()
  const form = useForm<WorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      payload: "",
      actionKey: "",
    },
  })
  const [selectedAction, setSelectedAction] = useState<Action | null>(null)

  const onSubmit = async (values: WorkflowControlsForm) => {
    console.log(values)
    console.log(selectedAction)
    // Make the API call to start the workflow
    await triggerWorkflow(
      session,
      workflow.id,
      values.actionKey,
      JSON.parse(values.payload)
    )
  }
  useEffect(() => {
    if (selectedAction) {
      form.setValue("actionKey", getActionKey(selectedAction))
    }
  }, [selectedAction])
  return (
    <Form {...form}>
      <form className="space-y-4">
        <div className="space-y-4 p-4">
          <div className="space-y-3">
            <h4 className="text-sm font-medium">Controls</h4>
          </div>
          <Separator />
          <FormField
            control={form.control}
            name="payload"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-xs">Send Payload</FormLabel>
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
          <div className="flex w-full items-center space-x-2">
            <EntrypointSelector
              selectedAction={selectedAction}
              setSelectedaction={setSelectedAction}
            />
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button type="button">
                  <div className="flex items-center space-x-2">
                    <Send className="h-4 w-4" />
                    <span>Send</span>
                  </div>
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    Start workflow confirmation
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    <span>
                      You are about to start the workflow with the selected
                      action:
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
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => form.handleSubmit(onSubmit)()}
                  >
                    <div className="flex items-center space-x-2">
                      <Send className="h-4 w-4" />
                      <span>Confirm</span>
                    </div>
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
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
          aria-label="Load a scenario..."
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
          <CommandInput className="text-xs" placeholder="Search scenarios..." />
          <CommandEmpty>No presets found.</CommandEmpty>
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
