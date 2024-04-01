"use client"

import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import { useSession } from "@/providers/session"
import { useWorkflowMetadata } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  Bird,
  CornerDownLeft,
  FileJson,
  FileType,
  Mic,
  Paperclip,
  Rabbit,
  Send,
  Settings,
  Share,
  Sheet,
  Turtle,
} from "lucide-react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import z from "zod"

import { Action } from "@/types/schemas"
import { stringToJSONSchema } from "@/types/validators"
import { streamGenerator } from "@/lib/api"
import { triggerWorkflow } from "@/lib/flow"
import { cn, getActionKey } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { tileIconMapping } from "@/components/workspace/action-node"

const typeSchema = z.object({
  type: z.string(),
})
const arbitraryKeyValuePairsSchema = z.record(z.any())
const consoleEventSchema = typeSchema.and(arbitraryKeyValuePairsSchema)

type ConsoleEvent = z.infer<typeof consoleEventSchema>
const workflowControlsFormSchema = z.object({
  payload: stringToJSONSchema, // json
  actionKey: z.string({ required_error: "Please select a webhook." }),
  mimeType: z.string({ required_error: "Please select an input data type." }),
})

type WorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

export function Console() {
  const methods = useForm<WorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      actionKey: "",
      mimeType: "",
      payload: "",
    },
  })

  return (
    <FormProvider {...methods}>
      <form className="h-full">
        <main className="grid h-full w-full flex-1 items-center gap-4 overflow-auto p-4 md:grid-cols-2 lg:grid-cols-6">
          <ConsolePanel className="col-span-2" />
          <ConsoleFeed className="col-span-4" />
        </main>
      </form>
    </FormProvider>
  )
}

const supportedInputTypes = [
  { mimeType: "text/plain", icon: FileType, description: "Plain Text" },
  {
    mimeType: "application/json",
    icon: FileJson,
    description: "JavaScript Object Notation",
  },
  {
    mimeType: "text/csv",
    icon: Sheet,
    description: "Comma-Separated Value Files",
  },
]

interface ConsolePanelProps extends React.HTMLAttributes<HTMLDivElement> {}
export function ConsolePanel({ className }: ConsolePanelProps) {
  const { control } = useFormContext<WorkflowControlsForm>()
  const { workflow } = useWorkflowMetadata()
  const [actions, setActions] = useState<Action[]>([])
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
    <div
      className={cn(
        "relative hidden h-full flex-col items-start gap-8 md:flex",
        className
      )}
    >
      <div className="flex h-full w-full flex-col gap-8">
        <fieldset className="grid gap-6 rounded-lg border p-4 shadow-sm">
          <legend className="-ml-1 px-1 text-sm font-medium">Settings</legend>
          <div className="grid gap-3">
            <Label htmlFor="model">Entrypoint</Label>
            <FormField
              control={control}
              name="actionKey"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Select
                      onValueChange={field.onChange}
                      defaultValue={field.value}
                    >
                      <SelectTrigger
                        id="actionKey"
                        className="items-start [&_[data-description]]:hidden"
                      >
                        <SelectValue placeholder="Select a webhook" />
                      </SelectTrigger>
                      <SelectContent>
                        {actions?.map((action, idx) => {
                          const { title, type, description } = action
                          const Icon = tileIconMapping[type]
                          const actionKey = getActionKey(action)
                          return (
                            <SelectItem key={idx} value={actionKey}>
                              <div className="flex items-center gap-3 text-muted-foreground">
                                {Icon && <Icon className="size-5" />}
                                <div className="grid gap-0.5">
                                  <span className="font-medium text-foreground">
                                    {title}
                                  </span>
                                  <p className="text-xs" data-description>
                                    {description || "No description available."}
                                  </p>
                                </div>
                              </div>
                            </SelectItem>
                          )
                        })}
                      </SelectContent>
                    </Select>
                  </FormControl>
                  <FormMessage className="p-3" />
                </FormItem>
              )}
            />
          </div>
          <div className="grid gap-3">
            <Label htmlFor="model">Input Type</Label>
            <FormField
              control={control}
              name="mimeType"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Select
                      onValueChange={field.onChange}
                      defaultValue={field.value}
                    >
                      <SelectTrigger
                        id="mimeType"
                        className="items-start [&_[data-description]]:hidden"
                      >
                        <SelectValue placeholder="Select an input data type" />
                      </SelectTrigger>
                      <SelectContent>
                        {supportedInputTypes?.map(
                          ({ mimeType, icon: Icon, description }, idx) => {
                            return (
                              <SelectItem key={idx} value={mimeType}>
                                <div className="flex items-center gap-3 text-muted-foreground">
                                  {Icon && <Icon className="size-5" />}
                                  <div className="grid gap-0.5">
                                    <span className="font-medium text-foreground">
                                      {mimeType}
                                    </span>
                                    <p className="text-xs" data-description>
                                      {description ||
                                        "No description available."}
                                    </p>
                                  </div>
                                </div>
                              </SelectItem>
                            )
                          }
                        )}
                      </SelectContent>
                    </Select>
                  </FormControl>
                  <FormMessage className="p-3" />
                </FormItem>
              )}
            />
          </div>
        </fieldset>
        <fieldset className="grid gap-6 rounded-lg border p-4 shadow-sm">
          <legend className="-ml-1 px-1 text-sm font-medium">Messages</legend>
          <div className="grid gap-3">
            <Label htmlFor="role">Role</Label>
            <Select defaultValue="system">
              <SelectTrigger>
                <SelectValue placeholder="Select a role" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">System</SelectItem>
                <SelectItem value="user">User</SelectItem>
                <SelectItem value="assistant">Assistant</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-3">
            <Label htmlFor="content">Content</Label>
            <Textarea
              id="content"
              placeholder="You are a..."
              className="min-h-[9.5rem]"
            />
          </div>
        </fieldset>
      </div>
    </div>
  )
}

interface ConsoleFeedProps extends React.HTMLAttributes<HTMLDivElement> {}
function ConsoleFeed({ className }: ConsoleFeedProps) {
  const session = useSession()
  const { workflowId } = useParams<{ workflowId: string }>()
  const [isStreaming, setIsStreaming] = useState(false)
  const [events, setEvents] = useState<ConsoleEvent[]>([])
  const { control, handleSubmit, watch } =
    useFormContext<WorkflowControlsForm>()

  const onSubmit = handleSubmit((values: WorkflowControlsForm) => {
    // Make the API call to start the workflow
    console.log("values", values)
    if (!values.actionKey) {
      console.error("No action key provided")
      toast({
        title: "No action key provided",
        description: "Please select an action to start the workflow.",
      })
      return
    }

    try {
      triggerWorkflow(session, workflowId, values.actionKey, values.payload)
      toast({
        title: "Workflow started",
        description: "The workflow has been started successfully.",
      })
    } catch (error) {
      console.error("Error starting workflow", error)
      toast({
        title: "Error starting workflow",
        description: "There was an error starting the workflow.",
      })
    }
  })

  useEffect(() => {
    const fetchEvents = async () => {
      setIsStreaming(() => true)

      const generator = streamGenerator("/events/subscribe", session, {
        method: "GET",
      })

      try {
        for await (const chunk of generator) {
          const jsonChunk = JSON.parse(chunk)
          console.log("jsonChunk", jsonChunk)
          const consoleEvent = consoleEventSchema.parse(jsonChunk)
          setEvents((events) => [...events, consoleEvent])
        }
      } catch (error) {
        console.error("Error reading stream:", error)
      } finally {
        setIsStreaming(() => false)
      }
    }
    fetchEvents()
  }, [workflowId])

  const inputPlaceholder = getInputPlaceholder(watch("mimeType"))

  return (
    <div
      className={cn(
        "relative flex h-full min-h-[50vh] flex-col rounded-xl border bg-muted/50 p-4 shadow-sm lg:col-span-4",
        className
      )}
    >
      <Badge variant="outline" className="absolute right-3 top-3 flex gap-2">
        <span
          className={cn(
            "flex h-2 w-2 rounded-full",
            isStreaming ? "bg-green-400" : "bg-gray-400"
          )}
        />
        <span>Feed</span>
      </Badge>
      {/* Pushes the textarea to the bottom */}
      <div className="flex-1" />
      {events.map((event, index) => (
        <div key={index} className="flex items-center gap-2 p-3">
          <Badge variant="outline" className="flex gap-2">
            <span
              className={cn(
                "flex h-2 w-2 rounded-full",
                isStreaming ? "bg-green-400" : "bg-gray-400"
              )}
            />
            <span>{event.type}</span>
          </Badge>
          <span className="text-sm text-foreground">
            {JSON.stringify(event, null, 2)}
          </span>
        </div>
      ))}

      <div className="relative overflow-hidden rounded-lg border bg-background focus-within:ring-1 focus-within:ring-ring">
        <Label htmlFor="payload" className="sr-only">
          Payload
        </Label>
        <FormField
          control={control}
          name="payload"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Textarea
                  {...field}
                  id="payload"
                  placeholder={inputPlaceholder}
                  className="min-h-12 resize-none border-0 p-3 shadow-none focus-visible:ring-0"
                />
              </FormControl>
              <FormMessage className="p-3" />
            </FormItem>
          )}
        />
        <div className="flex items-center p-3 pt-0">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" disabled>
                <Paperclip className="size-4" />
                <span className="sr-only">Attach file</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Attach File</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" disabled>
                <Mic className="size-4" />
                <span className="sr-only">Use Microphone</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Use Microphone</TooltipContent>
          </Tooltip>
          <Button onClick={onSubmit} size="sm" className="ml-auto gap-1.5">
            Send Payload
            <CornerDownLeft className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  )
}

function getInputPlaceholder(
  mimeType: (typeof supportedInputTypes)[number]["mimeType"]
) {
  switch (mimeType) {
    case "text/plain":
      return `Type a message here...`
    case "application/json":
      return `Define a JSON payload here...`
    case "text/csv":
      return `Upload a CSV file here...`
    default:
      return "Please select an input data type from the control panel"
  }
}
