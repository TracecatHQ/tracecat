import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useSession } from "@/providers/session"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleIcon, Save } from "lucide-react"
import { FormProvider, useForm } from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"
import { z } from "zod"

import { Action, IntegrationType } from "@/types/schemas"
import { getActionById, updateAction } from "@/lib/flow"
import { useIntegrationFormSchema } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
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
import { CollapsibleSection } from "@/components/collapsible-section"
import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import { ActionNodeType } from "@/components/workspace/canvas/action-node"
import { baseActionSchema } from "@/components/workspace/panel/action/schemas"
import {
  ActionFormArray,
  ActionFormInputs,
  ActionFormJSON,
  ActionFormSelect,
  ActionFormTextarea,
  processInputs,
} from "@/components/workspace/panel/common"

export function IntegrationForm({
  actionId,
  integrationType,
  workflowId,
}: {
  actionId: string
  integrationType: IntegrationType
  workflowId: string | null
}) {
  const queryClient = useQueryClient()
  const { setNodes } = useWorkflowBuilder()
  const session = useSession()
  const {
    fieldSchema,
    fieldConfig,
    isLoading: schemaLoading,
    integrationSpec,
  } = useIntegrationFormSchema(session, integrationType)
  const schema = baseActionSchema.merge(fieldSchema)
  type Schema = z.infer<typeof schema>

  const {
    data: action,
    isLoading,
    error,
  } = useQuery<Action, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: async ({ queryKey }) => {
      const [_, actionId, workflowId] = queryKey as [string, string, string]
      return await getActionById(session, actionId, workflowId)
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
  const methods = useForm<Schema>({
    resolver: zodResolver(schema),
    values: {
      title: action?.title ?? "",
      description: action?.description ?? "",
      ...(action?.inputs ? processInputs(action.inputs) : {}), // Unpack the inputs object
    },
  })

  // Loading state to defend in a user friendly way
  // against undefined schemas or data
  if (isLoading || schemaLoading) {
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

  if (!fieldSchema || !fieldConfig) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <div className="space-y-2">
          <AlertNotification
            level="info"
            message={`Action type ${integrationType} is not yet supported.`}
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

  const onSubmit = methods.handleSubmit((values) => {
    mutate(values)
  })

  const status = "online"
  return (
    <div className="flex flex-col overflow-auto">
      <FormProvider {...methods}>
        <form onSubmit={onSubmit} className="flex max-w-full overflow-auto">
          <div className="w-full space-y-4 p-4">
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
                control={methods.control}
                name="title"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Title</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        className="text-xs"
                        placeholder="Add action title..."
                        value={methods.watch("title", "")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={methods.control}
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
                <h4 className="text-m font-medium">Integration Inputs</h4>
                <div className="inline-block space-y-2 text-xs text-muted-foreground">
                  <p>{integrationSpec?.description}</p>
                </div>
                <div className="space-y-2 text-xs text-muted-foreground">
                  <h5 className="font-bold">Docstring</h5>
                  <p>{integrationSpec?.docstring}</p>
                </div>

                <div className="space-y-2 capitalize">
                  {Object.entries(fieldConfig).map(
                    ([inputKey, inputOption]) => {
                      const common = {
                        inputKey,
                        inputOption,
                      }
                      switch (inputOption.type) {
                        case "select":
                          return (
                            <ActionFormSelect<Schema>
                              key={inputKey}
                              defaultValue={action?.inputs?.[inputKey]}
                              {...common}
                            />
                          )
                        case "textarea":
                          return (
                            <ActionFormTextarea<Schema>
                              key={inputKey}
                              {...common}
                            />
                          )
                        case "json":
                          return (
                            <ActionFormJSON<Schema>
                              key={inputKey}
                              {...common}
                            />
                          )
                        case "array":
                          return (
                            <ActionFormArray<Schema>
                              key={inputKey}
                              {...common}
                            />
                          )
                        default:
                          return (
                            <ActionFormInputs<Schema>
                              key={inputKey}
                              {...common}
                            />
                          )
                      }
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
                    {JSON.stringify(methods.watch(), null, 2)}
                  </SyntaxHighlighter>
                </CollapsibleSection>
              </div>
            </div>
          </div>
        </form>
      </FormProvider>
    </div>
  )
}
