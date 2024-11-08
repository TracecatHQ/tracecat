"use client"

import "react18-json-view/src/style.css"

import React, { useCallback, useEffect, useState } from "react"
import {
  ActionControlFlow,
  ActionUpdate,
  ApiError,
  JoinStrategy,
  RegistryActionRead,
  RegistryActionValidateResponse,
} from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import {
  AlertTriangleIcon,
  CheckCheck,
  Database,
  Info,
  LayoutListIcon,
  Loader2Icon,
  LucideIcon,
  RepeatIcon,
  RotateCcwIcon,
  SettingsIcon,
  Shapes,
  SquareFunctionIcon,
  ToyBrickIcon,
} from "lucide-react"
import { Controller, FormProvider, useForm } from "react-hook-form"
import { type Node } from "reactflow"
import YAML from "yaml"

import { useAction, useWorkbenchRegistryActions } from "@/lib/hooks"
import { cn, itemOrEmptyString, slugify } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
import { CustomEditor } from "@/components/editor"
import { getIcon } from "@/components/icons"
import { JSONSchemaTable } from "@/components/jsonschema-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { ActionNodeData } from "@/components/workbench/canvas/action-node"
import {
  ControlFlowOptionsTooltip,
  ForEachTooltip,
  RetryPolicyTooltip,
  RunIfTooltip,
} from "@/components/workbench/panel/action-panel-tooltips"

// These are YAML strings
type ActionFormSchema = {
  title?: string
  description?: string
  inputs?: string
  control_flow: {
    for_each?: string
    run_if?: string
    retry_policy?: string
    options?: string
  }
}
type ControlFlowOptions = {
  start_delay?: number
  join_strategy?: JoinStrategy
}

const typeToLabel: Record<
  RegistryActionRead["type"],
  { label: string; icon: LucideIcon }
> = {
  udf: {
    label: "User Defined Function",
    icon: SquareFunctionIcon,
  },
  template: {
    label: "Template Action",
    icon: ToyBrickIcon,
  },
}

export function ActionPanel({
  node,
  workflowId,
}: {
  node: Node<ActionNodeData>
  workflowId: string
}) {
  const { workspaceId } = useWorkspace()
  const { validationErrors } = useWorkflow()
  const { action, actionIsLoading, updateAction, isSaving } = useAction(
    node.id,
    workspaceId,
    workflowId
  )
  const actionName = node.data.type
  const { getRegistryAction } = useWorkbenchRegistryActions()
  const registryAction = getRegistryAction(actionName)
  const { for_each, run_if, retry_policy, ...options } =
    action?.control_flow ?? {}
  const methods = useForm<ActionFormSchema>({
    values: {
      title: action?.title,
      description: action?.description,
      inputs: itemOrEmptyString(action?.inputs),
      control_flow: {
        for_each: for_each ? YAML.stringify(for_each) : "",
        run_if: run_if ? YAML.stringify(run_if) : "",
        retry_policy: retry_policy ? YAML.stringify(retry_policy) : "",
        options: options ? YAML.stringify(options) : "",
      },
    },
  })

  const [actionValidationErrors, setActionValidationErrors] =
    useState<RegistryActionValidateResponse | null>(null)
  const [saveState, setSaveState] = useState<"idle" | "saving" | "success">(
    "idle"
  )

  useEffect(() => {
    if (isSaving) {
      setSaveState("saving")
    } else {
      setSaveState("success")
      const timer = setTimeout(() => {
        setSaveState("idle")
      }, 2000)
      return () => clearTimeout(timer)
    }
  }, [isSaving])

  const handleSave = useCallback(
    async (values: ActionFormSchema) => {
      if (!registryAction || !action) {
        console.error("Action not found")
        return
      }
      const { inputs, title, description, control_flow } = values
      let actionInputs: Record<string, unknown>
      try {
        actionInputs = inputs ? YAML.parse(inputs) : {}
      } catch (error) {
        console.error("Failed to parse inputs", error)
        setActionValidationErrors({
          ok: false,
          message: "Failed to parse action inputs",
          detail: String(error),
        })
        return toast({
          title: "Couldn't parse action inputs",
          description: "Please see the error window for more details",
        })
      }
      const options: ControlFlowOptions | undefined = control_flow.options
        ? YAML.parse(control_flow.options)
        : undefined
      const actionControlFlow = {
        for_each: control_flow.for_each
          ? YAML.parse(control_flow.for_each)
          : undefined,
        run_if: control_flow.run_if
          ? YAML.parse(control_flow.run_if)
          : undefined,
        retry_policy: control_flow?.retry_policy
          ? YAML.parse(control_flow.retry_policy)
          : undefined,
        ...options,
      } as ActionControlFlow
      const params = {
        title: title,
        description: description,
        inputs: actionInputs,
        control_flow: actionControlFlow,
      } as ActionUpdate

      try {
        await updateAction(params)
      } catch (error) {
        if (error instanceof ApiError) {
          console.error("Application failed to validate action", error.body)
        } else {
          console.error("Validation failed, unknown error", error)
        }
      }
    },
    [workspaceId, registryAction, action]
  )

  const onSubmit = useCallback(
    async (values: ActionFormSchema) => {
      await handleSave(values)
    },
    [handleSave]
  )

  const onPanelBlur = useCallback(
    async (event: React.FocusEvent) => {
      // Save whenever focus changes, regardless of where it's going
      const values = methods.getValues()
      await handleSave(values)
    },
    [methods, handleSave]
  )

  const handleKeyDownPanel = useCallback(
    async (event: React.KeyboardEvent) => {
      // Check for Cmd+Enter (Mac) or Ctrl+Enter (Windows/Linux)
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        await handleSave(methods.getValues())
      }
    },
    [methods, handleSave]
  )

  if (actionIsLoading) {
    return <CenteredSpinner />
  }
  if (!registryAction) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <AlertNotification
          level="error"
          message={`Could not load action schema '${actionName}'.`}
        />
      </div>
    )
  }
  if (!action) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <AlertNotification
          level="error"
          message={`Error orccurred loading action.`}
        />
      </div>
    )
  }

  // If there are validation errors, filter out the errors related to this action
  const finalValErrors = validationErrors
    ?.filter((error) => error.action_ref === slugify(action.title))
    .concat(actionValidationErrors || [])
  const ActionIcon = typeToLabel[registryAction.type].icon
  return (
    <div
      className="size-full overflow-auto"
      onBlur={onPanelBlur}
      onKeyDown={handleKeyDownPanel}
      // Need tabIndex to receive blur events
      tabIndex={-1}
    >
      <Tabs defaultValue="inputs">
        <FormProvider {...methods}>
          <form
            onSubmit={methods.handleSubmit(onSubmit)}
            className="flex max-w-full flex-col overflow-auto"
          >
            <div className="relative">
              <h3 className="p-4 py-6">
                <div className="flex w-full items-start space-x-4">
                  <div className="flex-col">
                    {getIcon(registryAction.action, {
                      className: "size-10 p-2",
                      flairsize: "md",
                    })}
                  </div>
                  <div className="flex w-full flex-1 justify-between space-x-12">
                    <div className="flex flex-col">
                      <div className="flex w-full items-center justify-between text-xs font-medium leading-none">
                        <div className="flex w-full">{action.title}</div>
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {action.description || (
                          <span className="italic">No description</span>
                        )}
                      </p>
                      <div className="mt-2 hover:cursor-default">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="mt-2 flex items-center text-xs text-muted-foreground">
                              <ActionIcon className="mr-1 size-3 stroke-2" />
                              <span>
                                {typeToLabel[registryAction.type].label}
                              </span>
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="left" sideOffset={10}>
                            Action Type
                          </TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="mt-2 flex items-center text-xs text-muted-foreground">
                              <Database className="mr-1 size-3 stroke-2" />
                              <span>{registryAction.origin}</span>
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="left" sideOffset={10}>
                            Origin
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    </div>
                  </div>
                </div>
              </h3>
              <SaveStateIcon saveState={saveState} />
            </div>
            <div className="flex items-center justify-start">
              <TabsList className="grid h-8 grid-cols-3 rounded-none bg-transparent p-0">
                <TabsTrigger
                  className="size-full w-full min-w-[120px] rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="inputs"
                >
                  <LayoutListIcon className="mr-2 size-4" />
                  <span>Inputs</span>
                </TabsTrigger>
                <TabsTrigger
                  className="size-full w-full min-w-[120px] rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="control-flow"
                >
                  <RepeatIcon className="mr-2 size-4" />
                  <span>Control Flow</span>
                </TabsTrigger>
                <TabsTrigger
                  className="size-full w-full min-w-[120px] rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="retry-policy"
                >
                  <RotateCcwIcon className="mr-2 size-4" />
                  <span>Retry Policy</span>
                </TabsTrigger>
              </TabsList>
            </div>
            <Separator />
            <TabsContent value="inputs">
              {/* Metadata */}
              <Accordion
                type="multiple"
                defaultValue={["action-schema", "action-inputs"]}
                className="pb-10"
              >
                <AccordionItem value="action-settings">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <SettingsIcon className="mr-3 size-4" />
                      <span>General</span>
                    </div>
                  </AccordionTrigger>
                  {/* General settings for the action */}
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
                            <FormLabel className="text-xs">
                              Description
                            </FormLabel>
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
                      <div className="space-y-2">
                        <Label className="flex items-center gap-2 text-xs text-muted-foreground">
                          <span>Action ID</span>
                          <CopyButton
                            value={action.id}
                            toastMessage="Copied workflow ID to clipboard"
                          />
                        </Label>
                        <div className="rounded-md border shadow-sm">
                          <Input
                            value={action.id}
                            className="rounded-md border-none text-xs shadow-none"
                            readOnly
                            disabled
                          />
                        </div>
                      </div>
                    </div>
                  </AccordionContent>
                </AccordionItem>

                {/* Schema */}
                <AccordionItem value="action-schema">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <Shapes className="mr-3 size-4" />
                      <span>Input Schema</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4">
                    {/* Action secrets */}
                    <div className="space-y-4 px-4">
                      {registryAction.secrets &&
                      registryAction.secrets.length > 0 ? (
                        <div className="text-xs text-muted-foreground">
                          <span>
                            This action requires the following secrets:
                          </span>
                          <Table>
                            <TableHeader>
                              <TableRow className="h-6  text-xs capitalize">
                                <TableHead className="font-bold" colSpan={1}>
                                  Secret Name
                                </TableHead>
                                <TableHead className="font-bold" colSpan={1}>
                                  Required Keys
                                </TableHead>
                                <TableHead className="font-bold" colSpan={1}>
                                  Optional Keys
                                </TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {registryAction.secrets.map((secret, idx) => (
                                <TableRow
                                  key={idx}
                                  className="font-mono text-xs tracking-tight text-muted-foreground"
                                >
                                  <TableCell>{secret.name}</TableCell>
                                  <TableCell>
                                    {secret.keys?.join(", ") || "-"}
                                  </TableCell>
                                  <TableCell>
                                    {secret.optional_keys?.join(", ") || "-"}
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          No secrets required.
                        </span>
                      )}
                    </div>
                    {/* Action inputs */}
                    <div className="space-y-4 px-4">
                      <span className="text-xs text-muted-foreground">
                        Hover over each row for details.
                      </span>
                      <JSONSchemaTable
                        schema={registryAction.interface.expects}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>

                {/* Inputs */}
                <AccordionItem value="action-inputs">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <LayoutListIcon className="mr-3 size-4" />
                      <span>Inputs</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="flex flex-col space-y-4 px-4">
                      {!!finalValErrors && finalValErrors.length > 0 && (
                        <div className="flex items-center space-x-2">
                          <AlertTriangleIcon className="size-4 fill-rose-500 stroke-white" />
                          <span className="text-xs text-rose-500">
                            Validation errors occurred, please see below.
                          </span>
                        </div>
                      )}
                      <span className="text-xs text-muted-foreground">
                        Define action inputs in YAML below.
                      </span>
                      <Controller
                        name="inputs"
                        control={methods.control}
                        render={({ field }) => (
                          <CustomEditor
                            className="h-72 w-full"
                            defaultLanguage="yaml"
                            value={field.value}
                            onChange={field.onChange}
                          />
                        )}
                      />
                      {!!finalValErrors && finalValErrors.length > 0 && (
                        <div className="rounded-md border border-rose-400 bg-rose-100 p-4 font-mono text-xs text-rose-500">
                          <span className="font-bold">Validation Errors</span>
                          <Separator className="my-2 bg-rose-400" />
                          {finalValErrors.map((error, index) => (
                            <div key={index} className="mb-4">
                              <span>{error.message}</span>
                              <pre>{YAML.stringify(error.detail)}</pre>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </TabsContent>
            <TabsContent value="control-flow">
              <Accordion
                type="multiple"
                defaultValue={["action-control-flow"]}
                className="pb-10"
              >
                <AccordionItem value="action-control-flow">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <RepeatIcon className="mr-3 size-4" />
                      <span>Control Flow</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4">
                    {/* Run if */}
                    <div className="flex flex-col space-y-4 px-4">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>Run If</span>
                      </FormLabel>
                      <div className="flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <Info className="mr-1 size-3 stroke-muted-foreground" />
                          </HoverCardTrigger>
                          <HoverCardContent
                            className="w-auto max-w-[500px] p-3 font-mono text-xs tracking-tight"
                            side="left"
                            sideOffset={20}
                          >
                            <RunIfTooltip />
                          </HoverCardContent>
                        </HoverCard>

                        <span className="text-xs text-muted-foreground">
                          Define a conditional expression that determines if the
                          action executes.
                        </span>
                      </div>

                      <Controller
                        name="control_flow.run_if"
                        control={methods.control}
                        render={({ field }) => (
                          <CustomEditor
                            className="h-24 w-full"
                            defaultLanguage="yaml"
                            value={field.value}
                            onChange={field.onChange}
                          />
                        )}
                      />
                    </div>
                    {/* Loop */}
                    <div className="flex flex-col space-y-4 px-4">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>Loop Iteration</span>
                      </FormLabel>
                      <div className="flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <Info className="mr-1 size-3 stroke-muted-foreground" />
                          </HoverCardTrigger>
                          <HoverCardContent
                            className="w-auto max-w-[500px] p-3 font-mono text-xs tracking-tight"
                            side="left"
                            sideOffset={20}
                          >
                            <ForEachTooltip />
                          </HoverCardContent>
                        </HoverCard>

                        <span className="text-xs text-muted-foreground">
                          Define one or more loop expressions for the action to
                          iterate over.
                        </span>
                      </div>

                      <Controller
                        name="control_flow.for_each"
                        control={methods.control}
                        render={({ field }) => (
                          <CustomEditor
                            className="h-24 w-full"
                            defaultLanguage="yaml"
                            value={field.value}
                            onChange={field.onChange}
                          />
                        )}
                      />
                    </div>
                    {/* Other options */}
                    <div className="flex flex-col space-y-4 px-4">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>Options</span>
                      </FormLabel>
                      <div className="flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <Info className="mr-1 size-3 stroke-muted-foreground" />
                          </HoverCardTrigger>
                          <HoverCardContent
                            className="w-auto max-w-[500px] p-3 font-mono text-xs tracking-tight"
                            side="left"
                            sideOffset={20}
                          >
                            <ControlFlowOptionsTooltip />
                          </HoverCardContent>
                        </HoverCard>

                        <span className="text-xs text-muted-foreground">
                          Define additional control flow options for the action.
                        </span>
                      </div>
                      <Controller
                        name="control_flow.options"
                        control={methods.control}
                        render={({ field }) => (
                          <CustomEditor
                            className="h-24 w-full"
                            defaultLanguage="yaml"
                            value={field.value}
                            onChange={field.onChange}
                          />
                        )}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </TabsContent>
            <TabsContent value="retry-policy">
              <Accordion
                type="multiple"
                defaultValue={["action-retry-policy"]}
                className="pb-10"
              >
                <AccordionItem value="action-retry-policy">
                  <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                    <div className="flex items-center">
                      <RotateCcwIcon className="mr-3 size-4" />
                      <span>Retry Policy</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4">
                    {/* Retry Policy */}
                    <div className="flex flex-col space-y-4 px-4">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>Retry Policy</span>
                      </FormLabel>
                      <div className="flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <Info className="mr-1 size-3 stroke-muted-foreground" />
                          </HoverCardTrigger>
                          <HoverCardContent
                            className="w-auto max-w-[500px] p-3 font-mono text-xs tracking-tight"
                            side="left"
                            sideOffset={20}
                          >
                            <RetryPolicyTooltip />
                          </HoverCardContent>
                        </HoverCard>

                        <span className="text-xs text-muted-foreground">
                          Define the retry policy for the action.
                        </span>
                      </div>
                      <Controller
                        name="control_flow.retry_policy"
                        control={methods.control}
                        render={({ field }) => (
                          <CustomEditor
                            className="h-24 w-full"
                            defaultLanguage="yaml"
                            value={field.value}
                            onChange={field.onChange}
                          />
                        )}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </TabsContent>
          </form>
        </FormProvider>
      </Tabs>
    </div>
  )
}
function SaveStateIcon({
  saveState,
}: {
  saveState: "idle" | "saving" | "success"
}) {
  return (
    <div
      className={cn(
        "animate-fade-out absolute right-4 top-4 flex justify-end space-x-2 transition-opacity duration-200",
        saveState === "idle" && "opacity-0"
      )}
    >
      {saveState === "saving" && (
        <Loader2Icon className="size-4 animate-spin text-muted-foreground/70" />
      )}
      {saveState === "success" && (
        <CheckCheck className="size-4 text-green-500" />
      )}
    </div>
  )
}
