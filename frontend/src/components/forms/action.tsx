import React, { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import { useWorkflowBuilder } from "@/providers/builder"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery } from "@tanstack/react-query"
import axios from "axios"
import { CircleIcon, Save } from "lucide-react"
import { useForm } from "react-hook-form"
import { Node } from "reactflow"
import { z } from "zod"

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
import { getActionSchema } from "@/components/forms/action-schemas"

interface ActionResponse {
  id: string
  title: string
  description: string
  status: string
  inputs: Record<string, any> | null
}

interface ActionFormProps {
  actionId: string
  actionType: string
}

export function ActionForm({
  actionId,
  actionType,
}: ActionFormProps): React.JSX.Element {
  const [status, setStatus] = useState("offline")
  const { setNodes } = useWorkflowBuilder()
  const params = useParams<{ id: string }>()
  const workflowId = params.id

  // Fetch Action by ID and Workflow ID
  const getActionById = async (): Promise<ActionResponse> => {
    try {
      const response = await axios.get<ActionResponse>(
        `http://localhost:8000/actions/${actionId}?workflow_id=${workflowId}`
      )
      // TODO: Validate response data with zod
      return response.data
    } catch (error) {
      console.error("Error fetching action:", error)
      throw error // Rethrow the error to ensure it's caught by useQuery's isError state
    }
  }

  const {
    data: actionResponseData,
    isLoading,
    isError,
  } = useQuery<ActionResponse, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: getActionById,
  })

  const { actionSchema, actionFieldSchema } = getActionSchema(actionType) ?? {}
  // Extend the Zod schema dynamically based on the fetched schema
  const actionFormSchema = actionSchema
    ? actionSchema.extend({
        title: z.string(),
        description: z.string(),
      })
    : z.object({
        title: z.string(),
        description: z.string(),
      })
  type actionFormSchemaType = z.infer<typeof actionFormSchema>

  const form = useForm<actionFormSchemaType>({
    resolver: zodResolver(actionFormSchema),
  })

  const renderFormField = (
    inputKey: keyof actionFormSchemaType,
    inputField: any
  ) => {
    const fieldType = inputField.type
    const fieldProps = form.register(inputKey)

    switch (fieldType) {
      case "Select":
        return (
          <FormItem>
            <FormLabel className="text-xs">{inputKey}</FormLabel>
            <FormControl>
              <Select
                // NOTE: Need to manually unpack fieldProps and pass them to the Select component
                // to ensure the form state for this shadcn component is updated correctly
                value={form.watch(inputKey)} // Ensure the Select component uses the current field value
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
                  {inputField.options.map((option: string) => (
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
      case "Textarea":
        return (
          <FormItem>
            <FormLabel className="text-xs">{inputKey}</FormLabel>
            <FormControl>
              <Textarea {...fieldProps} className="text-xs" />
            </FormControl>
            <FormMessage />
          </FormItem>
        )
      default:
        return (
          <FormItem>
            <FormLabel className="text-xs">{inputKey}</FormLabel>
            <FormControl>
              <Input {...fieldProps} className="text-xs" />
            </FormControl>
            <FormMessage />
          </FormItem>
        )
    }
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

  // Submit form and update Action
  async function updateAction(actionId: string, values: actionFormSchemaType) {
    const { title, description, ...inputsObject } = values
    const inputs = JSON.stringify(inputsObject)
    const updateActionParams = {
      title: values.title,
      description: values.description,
      inputs: inputs,
    }
    console.log("Updating action with:", updateActionParams)
    const response = await axios.post(
      `http://localhost:8000/actions/${actionId}`,
      JSON.stringify(updateActionParams),
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )
    return response.data // Adjust based on what your API returns
  }

  function useUpdateAction(actionId: string) {
    const mutation = useMutation({
      mutationFn: (values: actionFormSchemaType) =>
        updateAction(actionId, values),
      // Configure your mutation behavior here
      onSuccess: (data, variables, context) => {
        console.log("Action update successful", data)
        setNodes((nds: Node[]) =>
          nds.map((node: Node) => {
            if (node.id === actionId) {
              node.data = {
                ...node.data,
                title: data.title,
                status: data.status,
                isConfigured: data.inputs ? true : false,
              }
            }
            return node
          })
        )
      },
      onError: (error, variables, context) => {
        console.error("Failed to update action:", error)
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
                        className="text-xs"
                        placeholder="Add action name..."
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
                        placeholder="Describe your action..."
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Separator />
              <div className="space-y-2 capitalize">
                <h4 className="text-m font-medium">Action Inputs</h4>
                <p className="text-xs text-muted-foreground">
                  Define the inputs for this action.
                </p>
                {Object.entries(actionFieldSchema).map(
                  ([inputKey, inputField]) => {
                    return (
                      <FormField
                        key={inputKey}
                        control={form.control}
                        name={inputKey as keyof actionFormSchemaType}
                        render={() =>
                          renderFormField(
                            inputKey as keyof actionFormSchemaType,
                            inputField
                          )
                        }
                      />
                    )
                  }
                )}
              </div>
            </div>
          </div>
        </form>
      </Form>
    </ScrollArea>
  )
}
