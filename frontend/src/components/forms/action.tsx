import React, { useCallback, useEffect, useState } from "react"
import { useParams } from "next/navigation"
import { useWorkflowBuilder } from "@/providers/builder"
import { useSession } from "@/providers/session"
import { ActionType } from "@/types"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery } from "@tanstack/react-query"
import { CircleIcon, Save } from "lucide-react"
import { ControllerRenderProps, useForm } from "react-hook-form"
import { z } from "zod"

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
  ActionFieldOption,
  getActionSchema,
} from "@/components/forms/action-schemas"

const baseActionSchema = z.object({
  title: z.string(),
  description: z.string(),
})
export type BaseActionSchema = z.infer<typeof baseActionSchema>

interface ActionFormProps {
  actionId: string
  actionType: ActionType
}

export function ActionForm({
  actionId,
  actionType,
}: ActionFormProps): React.JSX.Element {
  const [status, setStatus] = useState<ActionStatus>("offline")
  const { setNodes } = useWorkflowBuilder()
  const { workflowId } = useParams<{ workflowId: string }>()
  const session = useSession()

  const { data: actionResponseData } = useQuery<Action, Error>({
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

  const { actionSchema, actionFieldSchema } = getActionSchema(actionType)

  // Extend the Zod schema dynamically based on the fetched schema
  const actionFormSchema = actionSchema
    ? baseActionSchema.merge(actionSchema)
    : baseActionSchema
  type actionFormSchemaType = z.infer<typeof actionFormSchema>

  const form = useForm<actionFormSchemaType>({
    resolver: zodResolver(actionFormSchema),
  })

  const renderFormField = useCallback(
    (
      inputKey: keyof actionFormSchemaType,
      inputField: ActionFieldOption,
      fieldProps: ControllerRenderProps<actionFormSchemaType>
    ) => {
      switch (inputField.type) {
        case "select":
          if (!inputField.options) return <></>
          return (
            <FormItem>
              <FormLabel className="text-xs">{inputKey}</FormLabel>
              <FormControl>
                <Select
                  // NOTE: Need to manually unpack fieldProps and pass them to the Select component
                  // to ensure the form state for this shadcn component is updated correctly
                  value={form.watch(inputKey)} // Ensure the Select component uses the current field value
                  defaultValue={actionResponseData?.inputs?.[inputKey]} // Set the default value from the fetched action data
                  onValueChange={
                    (value) => {
                      fieldProps.onChange({
                        target: {
                          value: value,
                        },
                      })
                      form.setValue(inputKey, value)
                    } // Update the form state on change
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {inputField.options?.map((option: string) => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormControl>
              <FormMessage />
            </FormItem>
          )
        case "textarea":
          return (
            <FormItem>
              <FormLabel className="text-xs">{inputKey}</FormLabel>
              <FormControl>
                <Textarea
                  {...fieldProps}
                  className="text-xs"
                  value={form.watch(inputKey, "")}
                  placeholder="Input text here..."
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )
        case "json":
          return (
            <FormItem>
              <FormLabel className="text-xs">{inputKey}</FormLabel>
              <FormControl>
                <pre>
                  <Textarea
                    {...fieldProps}
                    className="text-xs"
                    value={form.watch(inputKey, "")}
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
              <FormLabel className="text-xs">{inputKey}</FormLabel>
              <FormControl>
                <Input
                  {...fieldProps}
                  className="text-xs"
                  value={form.watch(inputKey, "")}
                  placeholder="Input a list of comma-separated values here..."
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )
        default:
          return (
            <FormItem>
              <FormLabel className="text-xs">{inputKey}</FormLabel>
              <FormControl>
                <Input
                  {...fieldProps}
                  className="text-xs"
                  value={form.watch(inputKey, "")}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )
      }
    },
    [form, actionResponseData]
  )

  useEffect(() => {
    if (actionResponseData) {
      const { title, description, status, inputs } = actionResponseData
      form.reset()
      form.reset({
        // Use reset method to set form values
        title: title,
        description: description,
        ...(inputs ? processInputs(inputs) : {}), // Process and unpack the inputs object
      })
      setStatus(status)
    }
  }, [actionResponseData, form.reset])

  function useUpdateAction(actionId: string) {
    const mutation = useMutation({
      mutationFn: (values: actionFormSchemaType) =>
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

    return mutation
  }

  const { mutate } = useUpdateAction(actionId)
  function onSubmit(values: actionFormSchemaType) {
    mutate(values)
  }

  // Loading state to defend in a user friendly way
  // against undefined schemas or data
  if (!actionResponseData || !actionFormSchema || !actionFieldSchema) {
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
                  {Object.entries(actionFieldSchema).map(
                    ([inputKey, inputField]) => {
                      return (
                        <FormField
                          key={inputKey}
                          control={form.control}
                          name={inputKey as keyof actionFormSchemaType}
                          render={({ field }) =>
                            renderFormField(
                              inputKey as keyof actionFormSchemaType,
                              inputField,
                              field
                            )
                          }
                        />
                      )
                    }
                  )}
                </div>
              </div>
            </div>
          </div>
        </form>
      </Form>
    </ScrollArea>
  )
}

const processInputs = (inputs: Record<string, any>): Record<string, any> => {
  return Object.entries(inputs).reduce(
    (stringInputs: Record<string, any>, [key, value]) => {
      // Check if value is an object and not null, not an array, and not a Date instance
      if (!value) {
        stringInputs[key] = ""
      } else if (
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value) &&
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
