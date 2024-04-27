import { useParams } from "next/navigation"
import { useEventFeedContext } from "@/providers/event-feed-stream"
import { CloudOff, CornerDownLeft, Loader, Mic, Paperclip } from "lucide-react"
import { useFormContext } from "react-hook-form"

import { triggerWorkflow } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { WorkflowControlsForm } from "@/components/console/console"
import { supportedInputTypes } from "@/components/console/control-panel"
import { EventFeedItem } from "@/components/console/event-feed-item"

interface ConsoleFeedProps extends React.HTMLAttributes<HTMLDivElement> {}
export function ConsoleFeed({ className }: ConsoleFeedProps) {
  const { workflowId } = useParams<{ workflowId: string }>()
  const { events, isStreaming } = useEventFeedContext()
  const { control, handleSubmit, watch } =
    useFormContext<WorkflowControlsForm>()

  const onSubmit = handleSubmit((values: WorkflowControlsForm) => {
    // Make the API call to start the workflow
    if (!values.actionKey) {
      console.error("No action key provided")
      toast({
        title: "No action key provided",
        description: "Please select an action to start the workflow.",
      })
      return
    }

    try {
      triggerWorkflow(workflowId, values.actionKey, values.payload)
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

  const inputPlaceholder = getInputPlaceholder(watch("mimeType"))

  return (
    <div
      className={cn(
        "relative flex h-full min-h-[50vh] flex-col space-y-4 lg:col-span-4",
        className
      )}
    >
      <div className="no-scrollbar flex-1 space-y-2 overflow-auto rounded-lg border bg-muted/30 p-4 shadow-sm">
        <div className="mt-4 space-y-4">
          {events?.map((event, index) => (
            <EventFeedItem key={index} {...event} />
          ))}
        </div>
        <LoadingDivider isStreaming={isStreaming} />
      </div>

      <div className="relative overflow-hidden rounded-lg border bg-background shadow-sm focus-within:ring-1 focus-within:ring-ring">
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

function LoadingDivider({ isStreaming }: { isStreaming: boolean }) {
  return (
    <div className="relative">
      <div className="absolute inset-0 flex items-center">
        <span className="w-full border-t" />
      </div>
      <div className="relative flex justify-center text-xs uppercase">
        <span className="bg-background px-2 text-muted-foreground">
          <div className="flex w-full items-center justify-center gap-2">
            {isStreaming ? (
              <>
                <Loader className="size-4 animate-[spin_2s_linear_infinite] stroke-muted-foreground/80" />
                <span>Waiting for events</span>
              </>
            ) : (
              <>
                <CloudOff className="size-4 stroke-muted-foreground/80" />
                <span>Feed disconnected</span>
              </>
            )}
          </div>
        </span>
      </div>
    </div>
  )
}
