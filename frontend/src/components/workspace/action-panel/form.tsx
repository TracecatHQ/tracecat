import { useWorkflowBuilder } from "@/providers/builder"
import { useSession } from "@/providers/session"
import { zodResolver } from "@hookform/resolvers/zod"
import { CopyIcon } from "@radix-ui/react-icons"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleIcon, DeleteIcon, Save } from "lucide-react"
import {
  FieldValues,
  useFieldArray,
  useForm,
  UseFormReturn,
} from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"
import { z } from "zod"

import { Action, ActionType } from "@/types/schemas"
import { getActionById, updateAction } from "@/lib/flow"
import { cn, copyToClipboard, undoSlugify } from "@/lib/utils"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CollapsibleSection } from "@/components/collapsible-section"
import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import { ActionNodeType } from "@/components/workspace/action-node"
import {
  ActionFieldOption,
  baseActionSchema,
  getSubActionSchema,
} from "@/components/workspace/action-panel/schemas"

function processInputs(inputs: Record<string, any>): Record<string, any> {
  return Object.entries(inputs).reduce(
    (newInputs: Record<string, any>, [key, value]) => {
      if (value === null || value === undefined) {
        // Is null or undefined
        newInputs[key] = ""
      } else if (
        // Is a serializable object (not an array or date)
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value) &&
        !(value instanceof Date)
      ) {
        newInputs[key] = JSON.stringify(value) // Stringify object values
      } else {
        // Includes arrays
        newInputs[key] = value // Keep non-object values as is
      }
      return newInputs
    },
    {}
  )
}

export function ActionForm({
  actionId,
  actionType,
  workflowId,
}: {
  actionId: string
  actionType: ActionType
  workflowId: string | null
}) {
  const queryClient = useQueryClient()
  const { setNodes } = useWorkflowBuilder()
  const session = useSession()

  // Set the schema for the action type
  const { subActionSchema, fieldSchema } = getSubActionSchema(actionType)

  const schema = baseActionSchema.merge(subActionSchema)
  type Schema = z.infer<typeof schema>

  const {
    data: action,
    isLoading,
    error,
  } = useQuery<Action, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: async ({ queryKey }) => {
      // Fetch Action by ID and Workflow ID
      const [_, actionId, workflowId] = queryKey as [string, string, string]
      const result = await getActionById(session, actionId, workflowId)
      return result
    },
  })

  const { mutate } = useMutation({
    mutationFn: (values: Schema) => updateAction(session, actionId, values),
    onSuccess: (data: Action) => {
      setNodes((nds: ActionNodeType[]) =>
        nds.map((node: ActionNodeType) => {
          if (node.id === actionId) {
            const { title } = data
            node.data = {
              ...node.data, // Overwrite the existing node data
              title,
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
      queryClient.invalidateQueries({
        queryKey: ["selected_action", actionId, workflowId],
      })
      queryClient.invalidateQueries({
        queryKey: ["workflow", workflowId],
      })
    },
    onError: (error) => {
      console.error("Failed to update action:", error)
      toast({
        title: "Failed to save action",
        description: "Could not update your action. Please try again.",
      })
    },
  })
  // Set the initial form values
  const form = useForm<Schema>({
    resolver: zodResolver(schema),
    values: {
      title: action?.title ?? "",
      description: action?.description ?? "",
      ...(action?.inputs ? processInputs(action.inputs) : {}), // Unpack the inputs object
    },
  })

  if (!subActionSchema) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <div className="space-y-2">
          <AlertNotification
            level="info"
            message={`Action type ${actionType} is not yet supported.`}
            reset={() =>
              queryClient.invalidateQueries({
                queryKey: ["selected_action", actionId, workflowId],
              })
            }
          />
        </div>
      </div>
    )
  }

  // Loading state to defend in a user friendly way
  // against undefined schemas or data
  if (isLoading) {
    // TODO: Make this loading state look more like a form
    return <FormLoading />
  }
  if (error) {
    return (
      <div className="flex items-center space-x-2 p-4">
        <div className="space-y-2">
          <AlertNotification
            level="error"
            message="Error occurred when loading action"
            reset={() =>
              queryClient.invalidateQueries({
                queryKey: ["selected_action", actionId, workflowId],
              })
            }
          />
        </div>
      </div>
    )
  }

  const onSubmit = form.handleSubmit((values) => {
    mutate(values)
  })

  const status = "online"
  return (
    <Form {...form}>
      <div
        className="flex flex-col overflow-auto"
        id="INSIDE SCROLL"
        style={{ display: "block" }}
      >
        <form onSubmit={onSubmit} className="flex max-w-full overflow-auto">
          <div id="WRAPPER" className="max-w-full space-y-4 p-4">
            <div className="flex w-full flex-col space-y-3 overflow-hidden">
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
                      "capitalize text-muted-foreground",
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
                    <FormLabel className="text-xs">Title</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        className="text-xs"
                        placeholder="Add action title..."
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
              <div className="space-y-4">
                <h4 className="text-m font-medium">Action Inputs</h4>
                <div className="inline-block space-y-2 text-xs text-muted-foreground">
                  <p>
                    Define the inputs for this action here. You may use
                    templated JSONPath expressions in any type of field other
                    than list fields.
                  </p>
                  <p>For example, this expression:</p>
                  <pre>
                    <code>{"{{ $.my_action.output.some_data }}"}</code>
                  </pre>
                  <p>
                    points to the output data field `some_data` from an action
                    called `My Action`, with slug `my_action`. Select &apos;Copy
                    JSONPath&apos; from the action tile dropdown to copy the
                    slug. Note that the `output` field is a default field that
                    is available for all actions.
                  </p>
                </div>
                <div className="space-y-2 capitalize">
                  {Object.entries(fieldSchema).map(
                    ([inputKey, inputOption]) => {
                      const typedKey = inputKey as keyof Schema

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
                                      <FormLabelInner
                                        inputKey={inputKey}
                                        inputOption={inputOption}
                                        form={form}
                                      />
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
                                      <FormLabelInner
                                        inputKey={inputKey}
                                        inputOption={inputOption}
                                        form={form}
                                      />
                                    </FormLabel>
                                    <FormControl>
                                      <Textarea
                                        {...field}
                                        className="min-h-48 text-xs"
                                        value={form.watch(typedKey, "")}
                                        placeholder={
                                          inputOption.placeholder ??
                                          "Input text here..."
                                        }
                                      />
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )
                              case "json":
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      <FormLabelInner
                                        inputKey={inputKey}
                                        inputOption={inputOption}
                                        form={form}
                                      />
                                    </FormLabel>
                                    <FormControl>
                                      <pre>
                                        <Textarea
                                          {...field}
                                          className="min-h-48 text-xs"
                                          value={form.watch(typedKey, "")}
                                          placeholder={
                                            inputOption.placeholder ??
                                            "Input JSON here..."
                                          }
                                        />
                                      </pre>
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )
                              case "array":
                                // NOTE: Need to use this hook inside the render prop to ensure
                                // the form state is updated and reloaded correctly
                                const { fields, append, remove } =
                                  // eslint-disable-next-line react-hooks/rules-of-hooks
                                  useFieldArray<Schema>({
                                    control: form.control,
                                    name: inputKey,
                                  })
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      <FormLabelInner
                                        inputKey={inputKey}
                                        inputOption={inputOption}
                                        form={form}
                                      />
                                    </FormLabel>
                                    <div className="flex flex-col space-y-2">
                                      {fields.map((field, index) => {
                                        return (
                                          <div
                                            key={`${field.id}.${index}`}
                                            className="flex w-full items-center gap-2"
                                          >
                                            <FormControl>
                                              <Input
                                                className="text-xs"
                                                key={`${field.id}.${index}`}
                                                {...form.register(
                                                  // @ts-ignore
                                                  `${inputKey}.${index}` as const
                                                )}
                                                value={form.watch(
                                                  // @ts-ignore
                                                  `${inputKey}.${index}` as const,
                                                  ""
                                                )}
                                              />
                                            </FormControl>

                                            <Button
                                              type="button"
                                              variant="default"
                                              className="bg-red-400 p-0 px-3"
                                              onClick={() => remove(index)}
                                            >
                                              <DeleteIcon className="stroke-8 h-4 w-4 stroke-white" />
                                            </Button>
                                          </div>
                                        )
                                      })}
                                      <Button
                                        type="button"
                                        onClick={() => append("")}
                                      >
                                        Add Item
                                      </Button>
                                    </div>
                                    <FormMessage />
                                  </FormItem>
                                )
                              default:
                                return (
                                  <FormItem>
                                    <FormLabel className="text-xs">
                                      <FormLabelInner
                                        inputKey={inputKey}
                                        inputOption={inputOption}
                                        form={form}
                                      />
                                    </FormLabel>
                                    <FormControl>
                                      <Input
                                        {...field}
                                        className="text-xs"
                                        value={form.watch(typedKey, "")}
                                        placeholder={
                                          inputOption.placeholder ??
                                          "Input text here..."
                                        }
                                        disabled={inputOption.disabled}
                                      />
                                    </FormControl>
                                    <FormMessage />
                                  </FormItem>
                                )
                            }
                          }}
                        />
                      )
                    }
                  )}
                </div>
                <CollapsibleSection
                  node="JSON View"
                  showToggleText={false}
                  className="text-md truncate text-start font-medium"
                  size="lg"
                  iconSize="md"
                >
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
                    {JSON.stringify(form.watch(), null, 2)}
                  </SyntaxHighlighter>
                </CollapsibleSection>
              </div>
            </div>
          </div>
        </form>
      </div>
    </Form>
  )
}

function FormLabelInner<T extends FieldValues>({
  inputKey,
  inputOption,
  form,
}: {
  inputKey: string
  inputOption: ActionFieldOption
  form: UseFormReturn<T>
}) {
  const typedKey = inputKey as keyof T
  return (
    <div className="flex items-center space-x-2">
      <span>{undoSlugify(inputKey)}</span>
      {inputOption.optional && (
        <span className="text-muted-foreground"> (Optional)</span>
      )}
      {inputOption.copyable && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              className="m-0 h-4 w-4 p-0"
              onClick={() => {
                copyToClipboard({
                  // @ts-ignore
                  value: form.getValues(typedKey),
                  message: "Copied URL to clipboard",
                })
                toast({
                  title: "Copied to clipboard",
                  // @ts-ignore
                  description: `Copied ${typedKey} to clipboard.`,
                })
              }}
            >
              <CopyIcon className="h-3 w-3" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Copy</TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}
