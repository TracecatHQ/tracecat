import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  BracesIcon,
  LayoutListIcon,
  SaveIcon,
  SettingsIcon,
  Sparkles,
  ViewIcon,
} from "lucide-react"
import { FormProvider, useForm } from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneLight } from "react-syntax-highlighter/dist/cjs/styles/hljs"
import { z } from "zod"

import { Action, type ActionType } from "@/types/schemas"
import { getActionById, updateAction } from "@/lib/flow"
import { cn } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import {
  ActionNodeType,
  getTileColor,
  tileIconMapping,
  typeToNodeSubtitle,
} from "@/components/workspace/canvas/action-node"
import {
  baseActionSchema,
  getSubActionSchema,
} from "@/components/workspace/panel/action/schemas"
import {
  ActionFormArray,
  ActionFormFlatKVArray,
  ActionFormInputs,
  ActionFormJSON,
  ActionFormSelect,
  ActionFormTextarea,
  processInputs,
} from "@/components/workspace/panel/common"

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

  // Set the schema for the action type
  const { fieldSchema, fieldConfig } = getSubActionSchema(actionType)

  const schema = baseActionSchema.merge(fieldSchema)
  type Schema = z.infer<typeof schema>

  const {
    data: action,
    isLoading,
    error,
  } = useQuery<Action, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: async ({ queryKey }) => {
      const [, actionId, workflowId] = queryKey as [string, string, string]
      return await getActionById(actionId, workflowId)
    },
  })

  const { mutate } = useMutation({
    mutationFn: (values: Schema) => updateAction(actionId, values),
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
  // TODO: More robust handling of undefined values
  const type = action?.type ?? "webhook"
  const title = action?.title ?? ""
  const description = action?.description ?? ""
  const subtitle = typeToNodeSubtitle[type as keyof typeof typeToNodeSubtitle]
  const tileIcon = tileIconMapping[actionType] ?? Sparkles

  const methods = useForm<Schema>({
    resolver: zodResolver(schema),
    values: {
      title: title,
      description: description,
      ...(action?.inputs ? processInputs(action.inputs) : {}), // Unpack the inputs object
    },
  })

  if (!fieldSchema) {
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

  const onSubmit = methods.handleSubmit((values) => {
    mutate(values)
  })

  return (
    <div className="size-full overflow-auto">
      <FormProvider {...methods}>
        <form
          onSubmit={onSubmit}
          className="flex max-w-full flex-col overflow-auto"
        >
          <div className="grid grid-cols-3">
            <div className="col-span-2 overflow-hidden">
              <h3 className="p-4 px-4">
                <div className="flex w-full items-center space-x-4">
                  <Avatar>
                    <AvatarFallback className={cn(getTileColor(type))}>
                      {React.createElement(tileIcon, { className: "h-5 w-5" })}
                    </AvatarFallback>
                  </Avatar>
                  <div className="flex w-full flex-1 justify-between space-x-12">
                    <div className="flex flex-col">
                      <div className="flex w-full items-center justify-between text-xs font-medium leading-none">
                        <div className="flex w-full">
                          {title}
                          {type.startsWith("llm.") && (
                            <Sparkles className="ml-2 h-3 w-3 fill-yellow-500 text-yellow-500" />
                          )}
                        </div>
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {description || subtitle}
                      </p>
                    </div>
                  </div>
                </div>
              </h3>
            </div>
            <div className="flex justify-end space-x-2 p-4">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button type="submit" size="icon">
                    <SaveIcon className="size-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Save</TooltipContent>
              </Tooltip>
            </div>
          </div>
          <Separator />
          {/* Metadata */}
          <Accordion type="single" defaultValue="action-inputs" collapsible>
            <AccordionItem value="action-settings">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-center">
                  <SettingsIcon className="mr-3 size-4" />
                  <span>General</span>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="my-4 space-y-2 px-4">
                  <FormField
                    control={methods.control}
                    name="title"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-xs">Name</FormLabel>
                        <FormControl>
                          <Input
                            className="text-xs"
                            placeholder="Name your workflow..."
                            {...field}
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
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="action-inputs">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-center">
                  <LayoutListIcon className="mr-3 size-4" />
                  <span>Inputs</span>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="px-4">
                  <Tabs defaultValue="gui">
                    <div className="flex flex-1 justify-end">
                      <TabsList className="mb-4">
                        <TabsTrigger className="text-xs" value="gui">
                          <ViewIcon className="mr-2 size-3.5" />
                          <span>View</span>
                        </TabsTrigger>
                        <TabsTrigger className="text-xs" value="json">
                          <BracesIcon className="mr-2 size-3.5" />
                          <span>JSON</span>
                        </TabsTrigger>
                      </TabsList>
                    </div>
                    <TabsContent value="gui">
                      <div className="space-y-4">
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
                              case "flat-kv":
                                return (
                                  <ActionFormFlatKVArray<Schema>
                                    key={inputKey}
                                    keyName="tag"
                                    valueName="value"
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
                    </TabsContent>
                    <TabsContent value="json">
                      <SyntaxHighlighter
                        language="json"
                        style={atomOneLight}
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
                            "rounded-lg p-4 overflow-auto max-w-full w-full",
                        }}
                      >
                        {JSON.stringify(methods.watch(), null, 2)}
                      </SyntaxHighlighter>
                    </TabsContent>
                  </Tabs>
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </form>
      </FormProvider>
    </div>
  )
}
