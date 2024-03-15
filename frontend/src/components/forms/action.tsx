import React, { useEffect, useState } from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useSession } from "@/providers/session"
import { ActionType } from "@/types"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery } from "@tanstack/react-query"
import { CircleIcon, Save } from "lucide-react"
import { useForm } from "react-hook-form"
import { ZodType } from "zod"

import { Action, ActionStatus } from "@/types/schemas"
import { getActionById, updateAction } from "@/lib/flow"
import { cn } from "@/lib/utils"
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
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { ActionNodeType } from "@/components/action-node"
import {
  ActionFieldSchema,
  DynamicSubActionForm,
  getActionSchema,
} from "@/components/forms/action-schemas"

const processInputs = (inputs: Record<string, any>): Record<string, any> => {
  return Object.entries(inputs).reduce(
    (stringInputs: Record<string, any>, [key, value]) => {
      if (!value) {
        // Is null or undefined
        stringInputs[key] = ""
      } else if (
        // Is a serializable object
        typeof value === "object" &&
        value !== null &&
        // !Array.isArray(value) &&
        !(value instanceof Date)
      ) {
        stringInputs[key] = JSON.stringify(value) // Stringify object values
      } else {
        stringInputs[key] = value // Keep non-object values as is
      }
      return stringInputs
    },
    {}
  )
}
interface ActionFormProps {
  actionId: string
  actionType: ActionType
  workflowId: string | null
}

export function ActionForm(props: ActionFormProps) {
  const { actionId, actionType, workflowId } = props
  const { setNodes } = useWorkflowBuilder()
  const session = useSession()

  const { data: action } = useQuery<Action, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: async ({ queryKey }) => {
      // Fetch Action by ID and Workflow ID
      const [_, actionId, workflowId] = queryKey as [string, string, string]
      console.log(
        "Fetching action with ID",
        actionId,
        "in workflow",
        workflowId
      )
      const result = await getActionById(session, actionId, workflowId)
      return result
    },
  })

  const { mutate } = useMutation({
    mutationFn: (values: DynamicSubActionForm) =>
      updateAction(session, actionId, values),
    onSuccess: (data, variables, context) => {
      setNodes((nds: ActionNodeType[]) =>
        nds.map((node: ActionNodeType) => {
          if (node.id === actionId) {
            node.data = {
              ...node.data, // Overwrite the existing node data
              ...data, // Update the node data with the new action data
              isConfigured: data.inputs !== null,
            }
          }
          return node
        })
      )
      console.log("Action update successful", data)
      toast({
        title: "Saved action",
        description: "Your action has been updated successfully.",
      })
    },
    onError: (error, variables, context) => {
      console.error("Failed to update action:", error)
      toast({
        title: "Failed to save action",
        description: "Could not update your action. Please try again.",
      })
    },
  })
  const onSubmit = (values: DynamicSubActionForm) => {
    // Need to handle parsing CSV
    console.log("Submitting action form with values", values)
    mutate(values)
  }
  const { actionSchema, actionFieldSchema } = getActionSchema(actionType)
  if (!actionSchema || !actionFieldSchema) {
    console.error(`Action schema for ${actionType} is unavailable`)
    return <span>Action schema unavailable.</span>
  }
  return (
    <ActionFormInternal
      action={action}
      actionSchema={actionSchema}
      actionFieldSchema={actionFieldSchema}
      onSubmit={onSubmit}
    />
  )
}
function ActionFormInternal({
  action,
  actionSchema,
  actionFieldSchema,
  onSubmit,
}: {
  action: Action | undefined
  actionSchema: ZodType<DynamicSubActionForm>
  actionFieldSchema: ActionFieldSchema
  onSubmit: (values: DynamicSubActionForm) => void
}) {
  const [status, setStatus] = useState<ActionStatus>("offline")

  const form = useForm<DynamicSubActionForm>({
    resolver: zodResolver(actionSchema),
    defaultValues: {
      title: "",
      description: "",
      ...Object.keys(actionFieldSchema).reduce(
        (acc, key) => ({ ...acc, [key]: "" }),
        {}
      ),
    },
  })

  useEffect(() => {
    if (action) {
      const { title, description, status, inputs } = action
      form.reset()
      form.reset({
        // Use reset method to set form values
        title,
        description,
        ...(inputs ? processInputs(inputs) : {}), // Process and unpack the inputs object
      })
      setStatus(status)
      form.setValue
    }
  }, [action, form.reset])

  // Loading state to defend in a user friendly way
  // against undefined schemas or data
  if (!action) {
    // TODO: Make this loading state look more like a form
    return (
      <div className="flex items-center space-x-2 p-4">
        <div className="space-y-2">
          <Skeleton className="h-4 w-[250px]" />
          <Skeleton className="h-4 w-[200px]" />
        </div>
      </div>
    )
  }

  return (
    <ScrollArea className="h-full">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)}>
          <div className="space-y-4 p-4">
            <div className="space-y-3">
              <h4 className="text-sm font-medium">Action Status</h4>
              <div className="flex justify-between">
                <Badge
                  variant="outline"
                  className={cn(
                    "px-4 py-1",
                    status === "online" ? "bg-green-100" : "bg-gray-100"
                  )}
                >
                  <CircleIcon
                    className={cn(
                      "mr-2 h-3 w-3",
                      status === "online"
                        ? "fill-green-600 text-green-600"
                        : "fill-gray-400 text-gray-400"
                    )}
                  />
                  <span
                    className={cn(
                      "text-muted-foreground",
                      status === "online" ? "text-green-600" : "text-gray-600"
                    )}
                  >
                    {status}
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
            <div className="mb-4 space-y-4">
              <FormField
                control={form.control}
                name="title"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Name</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        className="text-xs"
                        placeholder="Add action name..."
                        value={form.watch("title", "")}
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
                        {...field}
                        className="text-xs"
                        placeholder="Describe your action..."
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Separator />
              <div className="space-y-2">
                <h4 className="text-m font-medium">Action Inputs</h4>
                <p className="text-xs text-muted-foreground">
                  Define the inputs for this action. You may use templated
                  JSONPath expressions in your input, except for list-type
                  fields.
                </p>
                <p className="text-xs text-muted-foreground">
                  For example, "This {"{{ $.path.to.input }}"} is valid!"
                </p>
                <div className="capitalize">
                  {Object.entries(actionFieldSchema)
                    .filter(
                      ([key, _]) => key !== "title" && key !== "description"
                    )
                    .map(([inputKey, inputOption]) => {
                      const typedKey = inputKey as keyof DynamicSubActionForm
                      return (
                        <FormField
                          key={inputKey}
                          control={form.control}
                          name={typedKey}
                          render={({ field }) => {
                            switch (inputOption.type) {
                              case "select":
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      {inputKey}
                                    </FormLabel>
                                    <FormControl>
                                      <Select
                                        // NOTE: Need to manually unpack fieldProps and pass them to the Select component
                                        // to ensure the form state for this shadcn component is updated correctly
                                        value={form.watch(typedKey)} // Ensure the Select component uses the current field value
                                        defaultValue={
                                          action?.inputs?.[inputKey]
                                        } // Set the default value from the fetched action data
                                        onValueChange={
                                          (value) => {
                                            field.onChange({
                                              target: {
                                                value: value,
                                              },
                                            })
                                            form.setValue(typedKey, value)
                                          } // Update the form state on change
                                        }
                                      >
                                        <SelectTrigger>
                                          <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                          {inputOption.options?.map(
                                            (option: string) => (
                                              <SelectItem
                                                key={option}
                                                value={option}
                                              >
                                                {option}
                                              </SelectItem>
                                            )
                                          )}
                                        </SelectContent>
                                      </Select>
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )
                              case "textarea":
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      {inputKey}
                                    </FormLabel>
                                    <FormControl>
                                      <Textarea
                                        {...field}
                                        className="text-xs"
                                        value={form.watch(typedKey, "")}
                                        placeholder="Input text here..."
                                      />
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )
                              case "json":
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      {inputKey}
                                    </FormLabel>
                                    <FormControl>
                                      <pre>
                                        <Textarea
                                          {...field}
                                          className="text-xs"
                                          value={form.watch(typedKey, "")}
                                          placeholder="Input JSON here..."
                                        />
                                      </pre>
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )
                              case "array":
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      {inputKey}
                                    </FormLabel>
                                    <Input
                                      {...field}
                                      className="text-xs"
                                      value={form.watch(typedKey, "")}
                                    />
                                    <FormMessage />
                                  </FormItem>
                                )
                              default:
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      {inputKey}
                                    </FormLabel>
                                    <FormControl>
                                      <Input
                                        {...field}
                                        className="text-xs"
                                        value={form.watch(typedKey, "")}
                                      />
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )
                            }
                          }}
                        />
                      )
                    })}
                </div>
              </div>
            </div>
          </div>
        </form>
      </Form>
    </ScrollArea>
  )
}
