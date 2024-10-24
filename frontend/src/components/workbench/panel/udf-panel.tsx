"use client"

import "react18-json-view/src/style.css"

import React, { useCallback, useState } from "react"
import {
  ActionControlFlow,
  ApiError,
  registryActionsValidateRegistryAction,
  RegistryActionValidateResponse,
  UpdateActionParams,
} from "@/client"
import { useWorkspace } from "@/providers/workspace"
import {
  AlertTriangleIcon,
  Info,
  LayoutListIcon,
  RepeatIcon,
  SaveIcon,
  SettingsIcon,
  Shapes,
} from "lucide-react"
import { Controller, FormProvider, useForm } from "react-hook-form"
import { type Node } from "reactflow"
import YAML from "yaml"

import { usePanelAction, useWorkbenchRegistryActions } from "@/lib/hooks"
import { itemOrEmptyString } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
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
import { UDFNodeData } from "@/components/workbench/canvas/udf-node"
import {
  ControlFlowConfigTooltip,
  ForEachTooltip,
  RetryPolicyTooltip,
  RunIfTooltip,
} from "@/components/workbench/panel/udf-panel-tooltips"

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

export function UDFActionPanel({
  node,
  workflowId,
}: {
  node: Node<UDFNodeData>
  workflowId: string
}) {
  const { workspaceId } = useWorkspace()
  const { action, actionIsLoading, updateAction } = usePanelAction(
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

  const [validationErrors, setValidationErrors] =
    useState<RegistryActionValidateResponse | null>(null)

  const onSubmit = useCallback(
    async (values: ActionFormSchema) => {
      console.log("registry action", registryAction)
      console.log("action", action)
      if (!registryAction || !action) {
        console.error("UDF or action not found")
        return
      }
      const { inputs, title, description, control_flow } = values
      let actionInputs: Record<string, unknown>
      try {
        actionInputs = inputs ? YAML.parse(inputs) : {}
      } catch (error) {
        console.error("Failed to parse inputs", error)
        setValidationErrors({
          ok: false,
          message: "Failed to parse action inputs",
          detail: String(error),
        })
        return toast({
          title: "Couldn't parse action inputs",
          description: "Please see the error window for more details",
        })
      }
      const options: { start_delay?: number } | undefined = control_flow.options
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
        start_delay: options?.start_delay,
      } as ActionControlFlow
      try {
        const validateResponse = await registryActionsValidateRegistryAction({
          actionName: registryAction.action,
          requestBody: {
            args: actionInputs,
          },
        })
        console.log("Validation passed", validateResponse)
        if (!validateResponse.ok) {
          console.error("Validation failed", validateResponse)
          setValidationErrors(validateResponse)
          toast({
            title: "Validation Error",
            description: "Failed to validate action inputs",
          })
        } else {
          setValidationErrors(null)
          const params = {
            title: title as string,
            description: description as string,
            inputs: actionInputs,
            control_flow: actionControlFlow,
          } as UpdateActionParams
          console.log("Submitting action form", params)
          await updateAction(params)
        }
      } catch (error) {
        if (error instanceof ApiError) {
          console.error("Application failed to validate UDF", error.body)
        } else {
          console.error("Validation failed, unknown error", error)
        }
      }
    },
    [workspaceId, registryAction, action]
  )

  if (actionIsLoading) {
    return <CenteredSpinner />
  }
  if (!registryAction) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <AlertNotification
          level="error"
          message={`Could not load UDF schema '${actionName}'.`}
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
  console.log("registryAction", registryAction)

  return (
    <div className="size-full overflow-auto">
      <FormProvider {...methods}>
        <form
          onSubmit={methods.handleSubmit(onSubmit)}
          className="flex max-w-full flex-col overflow-auto"
        >
          <div className="grid grid-cols-3">
            <div className="col-span-2 overflow-hidden">
              <h3 className="p-4">
                <div className="flex w-full items-center space-x-4">
                  {getIcon(registryAction.action, {
                    className: "size-10 p-2",
                    flairsize: "md",
                  })}
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
                      <p className="mt-2 text-xs text-muted-foreground">
                        Type: {registryAction.type === "udf" && "UDF"}
                        {registryAction.type === "template" &&
                          "Template Action"}
                      </p>
                      <p className="mt-2 text-xs text-muted-foreground">
                        Origin: {registryAction.origin}
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
                {/* UDF secrets */}
                <div className="space-y-4 px-4">
                  {registryAction.secrets ? (
                    <div className="text-xs text-muted-foreground">
                      <span>This action requires the following secrets:</span>
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
                {/* UDF inputs */}
                <div className="space-y-4 px-4">
                  <span className="text-xs text-muted-foreground">
                    Hover over each row for details.
                  </span>
                  <JSONSchemaTable schema={registryAction.interface.expects} />
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
                  {validationErrors && (
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
                  {validationErrors && (
                    <div className="rounded-md border border-rose-400 bg-rose-100 p-4 font-mono text-xs text-rose-500">
                      <span className="font-bold">Validation Errors</span>
                      <Separator className="my-2 bg-rose-400" />
                      <span>{validationErrors.message}</span>
                      <pre>{YAML.stringify(validationErrors.detail)}</pre>
                    </div>
                  )}
                </div>
              </AccordionContent>
            </AccordionItem>
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
                      <HoverCardTrigger asChild className="hover:border-none">
                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="w-[500px] p-3 font-mono text-xs tracking-tight"
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
                      <HoverCardTrigger asChild className="hover:border-none">
                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="w-[500px] p-3 font-mono text-xs tracking-tight"
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
                {/* Retry Policy */}
                <div className="flex flex-col space-y-4 px-4">
                  <FormLabel className="flex items-center gap-2 text-xs font-medium">
                    <span>Retry Policy</span>
                  </FormLabel>
                  <div className="flex items-center">
                    <HoverCard openDelay={100} closeDelay={100}>
                      <HoverCardTrigger asChild className="hover:border-none">
                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="w-[500px] p-3 font-mono text-xs tracking-tight"
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
                {/* Other options */}
                <div className="flex flex-col space-y-4 px-4">
                  <FormLabel className="flex items-center gap-2 text-xs font-medium">
                    <span>Options</span>
                  </FormLabel>
                  <div className="flex items-center">
                    <HoverCard openDelay={100} closeDelay={100}>
                      <HoverCardTrigger asChild className="hover:border-none">
                        <Info className="mr-1 size-3 stroke-muted-foreground" />
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="w-[500px] p-3 font-mono text-xs tracking-tight"
                        side="left"
                        sideOffset={20}
                      >
                        <ControlFlowConfigTooltip />
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
        </form>
      </FormProvider>
    </div>
  )
}
