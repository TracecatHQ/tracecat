"use client"

import { useEffect, useState } from "react"
import * as React from "react"
import { useWorkflowMetadata } from "@/providers/workflow"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { CaretSortIcon } from "@radix-ui/react-icons"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@radix-ui/react-popover"
import { CheckIcon, Send } from "lucide-react"
import { useForm } from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"
import { z } from "zod"

import { Action, Workflow } from "@/types/schemas"
import { stringToJSONSchema } from "@/types/validators"
import { triggerWorkflow } from "@/lib/flow"
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
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"

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
type TWorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

export function WorkflowControlsForm({
  workflow,
}: {
  workflow: Workflow
}): React.JSX.Element {
  const [confirmationIsOpen, setConfirmationIsOpen] = useState(false)
  const form = useForm<TWorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      payload: "",
      actionKey: undefined,
    },
  })
  const [selectedAction, setSelectedAction] = useState<Action | null>(null)

  const onSubmit = (values: TWorkflowControlsForm) => {
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
      triggerWorkflow(workflow.id, values.actionKey, values.payload)
      setConfirmationIsOpen(false)
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
  }
  useEffect(() => {
    if (selectedAction) {
      console.log("Selected action", selectedAction)
      form.setValue("actionKey", getActionKey(selectedAction))
    }
  }, [selectedAction])
  return (
    <AlertDialog open={confirmationIsOpen}>
      <Form {...form}>
        <form className="space-y-4">
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
                wrapLongLines
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
        </form>
      </Form>
    </AlertDialog>
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
