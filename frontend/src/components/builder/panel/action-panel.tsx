"use client"

import "react18-json-view/src/style.css"

import React, { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { ActionUpdate, ApiError, ValidationResult } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  CircleCheckIcon,
  Database,
  FileTextIcon,
  InfoIcon,
  LayoutListIcon,
  LinkIcon,
  Loader2Icon,
  LucideIcon,
  MessagesSquare,
  Plus,
  RotateCcwIcon,
  SaveIcon,
  SettingsIcon,
  ShapesIcon,
  SplitIcon,
} from "lucide-react"
import { FormProvider, useForm } from "react-hook-form"
import { ImperativePanelHandle } from "react-resizable-panels"
import YAML from "yaml"
import { z } from "zod"

import { RequestValidationError, TracecatApiError } from "@/lib/errors"
import { useAction, useGetRegistryAction, useOrgAppSettings } from "@/lib/hooks"
import { PERMITTED_INTERACTION_ACTIONS } from "@/lib/interactions"
import { cn, slugify } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormDescription,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
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
import {
  ControlFlowOptionsTooltip,
  ForEachTooltip,
  RetryPolicyTooltip,
  RunIfTooltip,
} from "@/components/builder/panel/action-panel-tooltips"
import { CopyButton } from "@/components/copy-button"
import { DynamicCustomEditor } from "@/components/editor/dynamic"
import { getIcon } from "@/components/icons"
import { JSONSchemaTable } from "@/components/jsonschema-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { actionTypeToLabel } from "@/components/registry/icons"
import { ValidationErrorView } from "@/components/validation-errors"

// These are YAML strings
const actionFormSchema = z.object({
  title: z
    .string()
    .max(100, "Title must be less than 100 characters")
    .optional(),
  description: z
    .string()
    .max(500, "Description must be less than 500 characters")
    .optional(),
  inputs: z
    .string()
    .max(300000, "Inputs must be less than 300000 characters")
    .default(""),
  control_flow: z.object({
    for_each: z
      .string()
      .max(1000, "For each must be less than 1000 characters")
      .optional(),
    run_if: z
      .string()
      .max(1000, "Run if must be less than 1000 characters")
      .optional(),
    retry_policy: z
      .string()
      .max(1000, "Retry policy must be less than 1000 characters")
      .optional(),
    options: z
      .string()
      .max(1000, "Options must be less than 1000 characters")
      .optional(),
  }),
  is_interactive: z.boolean().default(false),
  interaction: z
    .discriminatedUnion("type", [
      z.object({
        type: z.literal("response"),
        timeout: z.number().nullish(),
      }),
      z.object({
        type: z.literal("approval"),
        timeout: z.number().nullish(),
      }),
    ])
    .optional(),
})
type ActionFormSchema = z.infer<typeof actionFormSchema>

const isControlFlowOption = (key: string) => {
  return [
    "control_flow.wait_until",
    "control_flow.join_strategy",
    "control_flow.start_delay",
  ].includes(key)
}

enum SaveState {
  IDLE = "idle",
  UNSAVED = "unsaved",
  SAVING = "saving",
  SAVED = "saved",
  ERROR = "error",
}
// Helper function to safely parse YAML
const parseYaml = (str: string | undefined) =>
  str ? YAML.parse(str) : undefined
const stringifyYaml = (obj: unknown | undefined) =>
  obj ? YAML.stringify(obj) : ""

export type ActionPanelTabs =
  | "inputs"
  | "control-flow"
  | "retry-policy"
  | "template-inputs"
export interface ActionPanelRef extends ImperativePanelHandle {
  setActiveTab: (tab: ActionPanelTabs) => void
  getActiveTab: () => ActionPanelTabs
  setOpen: (open: boolean) => void
  isOpen: () => boolean
}

export function ActionPanel({
  actionId,
  workflowId,
}: {
  actionId: string
  workflowId: string
}) {
  const { appSettings } = useOrgAppSettings()
  const { workspaceId } = useWorkspace()
  const { validationErrors } = useWorkflow()
  const { action, actionIsLoading, updateAction } = useAction(
    actionId,
    workspaceId,
    workflowId
  )
  const { actionPanelRef } = useWorkflowBuilder()
  const { registryAction, registryActionIsLoading, registryActionError } =
    useGetRegistryAction(action?.type)
  const { for_each, run_if, retry_policy, ...options } =
    action?.control_flow ?? {}
  const methods = useForm<ActionFormSchema>({
    resolver: zodResolver(actionFormSchema),
    values: {
      title: action?.title,
      description: action?.description,
      inputs: action?.inputs ?? "",
      control_flow: {
        for_each: stringifyYaml(for_each),
        run_if: stringifyYaml(run_if),
        retry_policy: stringifyYaml(retry_policy),
        options: stringifyYaml(options),
      },
      is_interactive: action?.is_interactive ?? false,
      interaction: action?.interaction ?? undefined,
    },
  })

  const [validationResults, setValidationResults] = useState<
    ValidationResult[]
  >([])
  const [saveState, setSaveState] = useState<SaveState>(SaveState.IDLE)
  const [activeTab, setActiveTab] = useState<ActionPanelTabs>("inputs")
  const [open, setOpen] = useState(false)

  useEffect(() => {
    setActiveTab("inputs")
    setSaveState(SaveState.IDLE)
    setValidationResults([])
  }, [actionId])

  // Set up the ref methods
  useEffect(() => {
    if (actionPanelRef.current) {
      actionPanelRef.current.setActiveTab = setActiveTab
      actionPanelRef.current.getActiveTab = () => activeTab
      actionPanelRef.current.setOpen = (newOpen: boolean) => {
        setOpen(newOpen)
        // If the panel has a collapse method, use it
        if (
          actionPanelRef.current?.collapse &&
          actionPanelRef.current?.expand
        ) {
          newOpen
            ? actionPanelRef.current.expand()
            : actionPanelRef.current.collapse()
        }
      }
      actionPanelRef.current.isOpen = () => open
    }
  }, [actionPanelRef, activeTab, setOpen, open])

  const handleSave = useCallback(
    async (values: ActionFormSchema) => {
      if (!registryAction || !action) {
        console.error("Action not found")
        return
      }

      setSaveState(SaveState.SAVING)
      setValidationResults([])

      try {
        const params: ActionUpdate = {
          title: values.title,
          description: values.description,
          inputs: values.inputs,
          control_flow: {
            ...parseYaml(values.control_flow.options), // Miscellaneous options
            for_each: parseYaml(values.control_flow.for_each),
            run_if: parseYaml(values.control_flow.run_if),
            retry_policy: parseYaml(values.control_flow.retry_policy),
          },
          is_interactive: values.is_interactive,
          interaction: values.interaction,
        }

        await updateAction(params)
        setTimeout(() => setSaveState(SaveState.SAVED), 300)
      } catch (error) {
        if (error instanceof ApiError) {
          const apiError = error as TracecatApiError
          console.error("Application failed to validate action", apiError.body)

          // Set form errors from API validation errors
          // NOTE: We do this dynamically because we already do server side validation.
          const errors: Record<string, { message: string }> = {}
          if (Array.isArray(apiError.body.detail)) {
            const valErrs = apiError.body.detail as RequestValidationError[]
            console.error("Validation errors", valErrs)
            valErrs.forEach(({ loc, msg }) => {
              let key: string = loc.slice(1).join(".")
              if (isControlFlowOption(key)) {
                key = "control_flow.options"
              }
              // Combine errors if they have the same key
              if (errors[key]) {
                errors[key].message += `\n${msg}`
              } else {
                errors[key] = { message: msg }
              }
            })
          } else {
            errors["inputs"] = { message: String(apiError.body.detail) }
          }
          Object.entries(errors).forEach(([key, { message }]) => {
            console.log("Setting error", key, message)
            methods.setError(key as keyof ActionFormSchema, {
              message,
            })
          })
          console.log("Errors", errors)
        } else {
          console.error("Validation failed, unknown error", error)
        }
        setSaveState(SaveState.ERROR)
      }
    },
    [
      registryAction,
      action,
      updateAction,
      methods,
      setSaveState,
      setValidationResults,
    ]
  )

  // If the form is dirty, set the save state to unsaved
  useEffect(() => {
    if (methods.formState.isDirty) {
      setSaveState(SaveState.UNSAVED)
    }
  }, [methods.formState.isDirty])

  const onSubmit = useCallback(
    async (values: ActionFormSchema) => {
      try {
        await handleSave(values)
      } catch (error) {
        console.error("Failed to save action", error)
        setSaveState(SaveState.ERROR)
        setValidationResults([
          {
            status: "error",
            msg: "Failed to save action",
            detail: [
              {
                type: "general",
                msg: String(error),
              },
            ],
            ref: slugify(action?.title ?? ""),
          },
        ])
      }
    },
    [handleSave, action]
  )

  const onPanelBlur = useCallback(() => {
    methods.handleSubmit(onSubmit)()
  }, [methods, onSubmit])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Save with Cmd+S (Mac) or Ctrl+S (Windows/Linux)
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault()
        methods.handleSubmit(onSubmit)()
      }
    }

    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [methods, onSubmit, action])

  if (actionIsLoading || registryActionIsLoading) {
    return <CenteredSpinner />
  }
  if (!registryAction || registryActionError) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <ActionPanelNotFound
          description={`Could not load action schema '${action?.type}'.`}
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
  const finalValErrors = [
    ...(validationResults || []),
    ...(validationErrors || []),
  ].filter((e) => e.ref === slugify(action.title))

  const ActionIcon = actionTypeToLabel[registryAction.type].icon
  const isInteractive = methods.watch("is_interactive")
  const interactionType = methods.watch("interaction.type")
  return (
    <div onBlur={onPanelBlur}>
      <Tabs
        defaultValue="inputs"
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as ActionPanelTabs)}
        className="w-full"
      >
        <FormProvider {...methods}>
          <form onSubmit={methods.handleSubmit(onSubmit)}>
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
                                {actionTypeToLabel[registryAction.type].label}
                              </span>
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="left" sideOffset={10}>
                            Action type
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
                        {registryAction.doc_url && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <div className="mt-2 flex items-center text-xs text-muted-foreground">
                                <LinkIcon className="mr-1 size-3 stroke-2" />
                                <Button
                                  variant="link"
                                  asChild
                                  className="h-auto p-0 text-xs text-muted-foreground"
                                >
                                  <Link
                                    href={registryAction.doc_url}
                                    target="_blank"
                                  >
                                    {registryAction.doc_url.length > 32
                                      ? registryAction.doc_url.substring(
                                          0,
                                          32
                                        ) + "..."
                                      : registryAction.doc_url}
                                  </Link>
                                </Button>
                              </div>
                            </TooltipTrigger>
                            <TooltipContent side="left" sideOffset={10}>
                              Link to docs
                            </TooltipContent>
                          </Tooltip>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
                <SaveStateIcon saveState={saveState} />
              </h3>
            </div>

            <div className="w-full min-w-[30rem]">
              <div className="flex items-center justify-start">
                <TabsList className="h-8 justify-start rounded-none bg-transparent p-0">
                  <TabsTrigger
                    className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="inputs"
                  >
                    <LayoutListIcon className="mr-2 size-4" />
                    <span>Inputs</span>
                  </TabsTrigger>
                  <TabsTrigger
                    className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="control-flow"
                  >
                    <SplitIcon className="mr-2 size-4" />
                    <span>If-condition / Loops</span>
                  </TabsTrigger>
                  <TabsTrigger
                    className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="retry-policy"
                  >
                    <RotateCcwIcon className="mr-2 size-4" />
                    <span>Retries</span>
                  </TabsTrigger>
                  {registryAction.is_template && (
                    <TabsTrigger
                      className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                      value="template-inputs"
                    >
                      <FileTextIcon className="mr-2 size-4" />
                      <span>View template</span>
                    </TabsTrigger>
                  )}
                </TabsList>
              </div>
              <Separator />
              <div className="w-full overflow-x-auto">
                <TabsContent value="inputs">
                  {/* Metadata */}
                  <Accordion
                    type="multiple"
                    defaultValue={["action-inputs"]}
                    className="pb-10"
                  >
                    <AccordionItem value="action-settings">
                      <AccordionTrigger className="px-4 text-xs font-bold">
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
                                    placeholder="Name your action..."
                                    {...field}
                                  />
                                </FormControl>
                                <FormMessage className="whitespace-pre-line" />
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
                                    placeholder="Describe your action..."
                                    {...field}
                                  />
                                </FormControl>
                                <FormMessage className="whitespace-pre-line" />
                              </FormItem>
                            )}
                          />
                          <div className="space-y-2">
                            <Label className="flex items-center gap-2 text-xs font-medium">
                              <span>Action ID</span>
                              <CopyButton
                                value={action.id}
                                toastMessage="Copied action ID to clipboard"
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

                    {/* Interaction */}
                    {appSettings?.app_interactions_enabled &&
                      PERMITTED_INTERACTION_ACTIONS.includes(
                        registryAction.action as (typeof PERMITTED_INTERACTION_ACTIONS)[number]
                      ) && (
                        <AccordionItem value="action-interaction">
                          <AccordionTrigger className="px-4 text-xs font-bold">
                            <div className="flex items-center">
                              <MessagesSquare className="mr-3 size-4" />
                              <span>Interaction</span>
                            </div>
                          </AccordionTrigger>
                          <AccordionContent>
                            <div className="my-4 space-y-2 px-4">
                              {/* Toggle for enabling interaction */}
                              <FormField
                                control={methods.control}
                                name="is_interactive"
                                render={({ field }) => (
                                  <FormItem>
                                    <div className="flex items-center gap-2">
                                      <FormControl>
                                        <Switch
                                          checked={field.value}
                                          onCheckedChange={field.onChange}
                                        />
                                      </FormControl>
                                      <FormLabel className="text-xs">
                                        Enable interaction
                                      </FormLabel>
                                    </div>
                                    <FormMessage className="whitespace-pre-line" />
                                  </FormItem>
                                )}
                              />

                              {/* Interaction settings - only shown when interaction is enabled */}
                              {isInteractive && (
                                <>
                                  <FormField
                                    control={methods.control}
                                    name="interaction.type"
                                    render={({ field }) => (
                                      <FormItem>
                                        <FormLabel className="text-xs">
                                          Type
                                        </FormLabel>
                                        <FormControl>
                                          <Select
                                            value={field.value}
                                            onValueChange={field.onChange}
                                          >
                                            <SelectTrigger className="text-xs">
                                              <SelectValue
                                                placeholder="Select a type..."
                                                className="text-xs"
                                              />
                                            </SelectTrigger>
                                            <SelectContent className="w-full text-xs">
                                              <SelectItem
                                                value="response"
                                                className="text-xs"
                                              >
                                                Response
                                              </SelectItem>
                                              <SelectItem
                                                value="approval"
                                                className="text-xs"
                                                disabled
                                              >
                                                <span>Approval</span>
                                                <Badge
                                                  variant="outline"
                                                  className="ml-4 text-xs font-normal"
                                                >
                                                  Coming soon
                                                </Badge>
                                              </SelectItem>
                                              <SelectItem
                                                value="mfa"
                                                className="text-xs"
                                                disabled
                                              >
                                                <span>
                                                  Multi-factor Authentication
                                                </span>
                                                <Badge
                                                  variant="outline"
                                                  className="ml-4 text-xs font-normal"
                                                >
                                                  Coming soon
                                                </Badge>
                                              </SelectItem>
                                              <SelectItem
                                                value="form"
                                                className="text-xs"
                                                disabled
                                              >
                                                <span>Form</span>
                                                <Badge
                                                  variant="outline"
                                                  className="ml-4 text-xs font-normal"
                                                >
                                                  Coming soon
                                                </Badge>
                                              </SelectItem>
                                            </SelectContent>
                                          </Select>
                                        </FormControl>
                                        <FormMessage className="whitespace-pre-line" />
                                      </FormItem>
                                    )}
                                  />

                                  {interactionType === "response" && (
                                    <div className="space-y-2">
                                      <FormDescription className="text-xs">
                                        The action will only complete when it
                                        receives a response.
                                      </FormDescription>
                                      <FormField
                                        control={methods.control}
                                        name="interaction.timeout"
                                        render={({ field }) => (
                                          <FormItem>
                                            <FormLabel className="text-xs">
                                              Timeout
                                            </FormLabel>
                                            <FormControl>
                                              <Input
                                                disabled
                                                type="number"
                                                value={field.value || ""}
                                                onChange={field.onChange}
                                                placeholder="Timeout in seconds"
                                                className="text-xs"
                                              />
                                            </FormControl>
                                            <FormMessage className="whitespace-pre-line" />
                                          </FormItem>
                                        )}
                                      />
                                    </div>
                                  )}
                                </>
                              )}
                            </div>
                          </AccordionContent>
                        </AccordionItem>
                      )}

                    {/* Schema */}
                    <AccordionItem value="action-schema">
                      <AccordionTrigger className="px-4 text-xs font-bold">
                        <div className="flex items-center">
                          <ShapesIcon className="mr-3 size-4" />
                          <span>Input schema</span>
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
                                    <TableHead
                                      className="font-bold"
                                      colSpan={1}
                                    >
                                      Secret Name
                                    </TableHead>
                                    <TableHead
                                      className="font-bold"
                                      colSpan={1}
                                    >
                                      Required Keys
                                    </TableHead>
                                    <TableHead
                                      className="font-bold"
                                      colSpan={1}
                                    >
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
                                        {secret.optional_keys?.join(", ") ||
                                          "-"}
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
                      <AccordionTrigger className="px-4 text-xs font-bold">
                        <div className="flex items-center">
                          <LayoutListIcon className="mr-3 size-4" />
                          <span>Inputs</span>
                        </div>
                      </AccordionTrigger>
                      <AccordionContent>
                        <div className="flex flex-col space-y-4 px-4">
                          {finalValErrors.length > 0 && (
                            <ValidationErrorView
                              validationErrors={finalValErrors}
                              side="left"
                              className="max-w-[600px]"
                            >
                              <div className="flex items-center space-x-2">
                                <AlertTriangleIcon className="size-4 fill-rose-500 stroke-white" />
                                <span className="pointer-events-none text-xs text-rose-500">
                                  Hover to view errors.
                                </span>
                              </div>
                            </ValidationErrorView>
                          )}
                          <span className="text-xs text-muted-foreground">
                            Define action inputs in YAML below.
                          </span>
                          <FormField
                            name="inputs"
                            control={methods.control}
                            render={({ field }) => (
                              <FormItem>
                                {/* Place form message above because it's not visible otherwise */}
                                <FormMessage className="whitespace-pre-line" />
                                <FormControl>
                                  <DynamicCustomEditor
                                    className="min-h-[40rem] w-full"
                                    value={field.value}
                                    onChange={field.onChange}
                                    defaultLanguage="yaml-extended"
                                    workspaceId={workspaceId}
                                    workflowId={workflowId}
                                  />
                                </FormControl>
                              </FormItem>
                            )}
                          />
                        </div>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                </TabsContent>
                <TabsContent value="control-flow">
                  <div className="mt-6 space-y-8 px-4">
                    {/* Run if */}
                    <div className="flex flex-col space-y-2">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>Run if</span>
                      </FormLabel>
                      <div className="mb-2 flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <InfoIcon className="mr-1 size-3 stroke-muted-foreground" />
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
                      <FormField
                        name="control_flow.run_if"
                        control={methods.control}
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <DynamicCustomEditor
                                className="h-24 w-full"
                                defaultLanguage="yaml-extended"
                                value={field.value}
                                onChange={field.onChange}
                                workspaceId={workspaceId}
                              />
                            </FormControl>
                            <FormMessage className="whitespace-pre-line" />
                          </FormItem>
                        )}
                      />
                    </div>
                    {/* Loop */}
                    <div className="flex flex-col space-y-2">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>For loops</span>
                      </FormLabel>
                      <div className="mb-2 flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <InfoIcon className="mr-1 size-3 stroke-muted-foreground" />
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
                      <FormField
                        name="control_flow.for_each"
                        control={methods.control}
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <DynamicCustomEditor
                                className="h-24 w-full"
                                defaultLanguage="yaml-extended"
                                value={field.value}
                                onChange={field.onChange}
                                workspaceId={workspaceId}
                                workflowId={workflowId}
                              />
                            </FormControl>
                            <FormMessage className="whitespace-pre-line" />
                          </FormItem>
                        )}
                      />
                    </div>
                    {/* Other options */}
                    <div className="flex flex-col space-y-2">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>Options</span>
                      </FormLabel>
                      <div className="mb-2 flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <InfoIcon className="mr-1 size-3 stroke-muted-foreground" />
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
                      <FormField
                        name="control_flow.options"
                        control={methods.control}
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <DynamicCustomEditor
                                className="h-24 w-full"
                                defaultLanguage="yaml-extended"
                                value={field.value}
                                onChange={field.onChange}
                                workspaceId={workspaceId}
                                workflowId={workflowId}
                              />
                            </FormControl>
                            <FormMessage className="whitespace-pre-line" />
                          </FormItem>
                        )}
                      />
                    </div>
                  </div>
                </TabsContent>
                <TabsContent value="retry-policy">
                  <div className="mt-6 space-y-8 px-4">
                    {/* Retry Policy */}
                    <div className="flex flex-col space-y-2">
                      <FormLabel className="flex items-center gap-2 text-xs font-medium">
                        <span>Retry policy</span>
                      </FormLabel>
                      <div className="mb-2 flex items-center">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger
                            asChild
                            className="hover:border-none"
                          >
                            <InfoIcon className="mr-1 size-3 stroke-muted-foreground" />
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
                      <FormField
                        name="control_flow.retry_policy"
                        control={methods.control}
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <DynamicCustomEditor
                                className="h-24 w-full"
                                defaultLanguage="yaml-extended"
                                value={field.value}
                                onChange={field.onChange}
                                workspaceId={workspaceId}
                                workflowId={workflowId}
                              />
                            </FormControl>
                            <FormMessage className="whitespace-pre-line" />
                          </FormItem>
                        )}
                      />
                    </div>
                  </div>
                </TabsContent>
                {/* Template */}
                {registryAction?.implementation && (
                  <TabsContent value="template-inputs">
                    <Accordion
                      type="multiple"
                      defaultValue={["action-template"]}
                      className="pb-10"
                    >
                      <AccordionItem value="action-template">
                        <AccordionTrigger className="px-4 text-xs font-bold">
                          <div className="flex items-center">
                            <FileTextIcon className="mr-3 size-4" />
                            <span>Template Definition</span>
                          </div>
                        </AccordionTrigger>
                        <AccordionContent className="space-y-4">
                          <div className="flex flex-col space-y-4 px-4">
                            <span className="text-xs text-muted-foreground">
                              Template action definition in YAML format.
                            </span>
                            <DynamicCustomEditor
                              className="min-h-[30rem] w-full"
                              value={YAML.stringify(
                                "type" in registryAction.implementation &&
                                  registryAction.implementation.type ===
                                    "template"
                                  ? registryAction.implementation
                                      .template_action
                                  : {},
                                null,
                                2
                              )}
                              defaultLanguage="yaml"
                              options={{
                                readOnly: true,
                                minimap: {
                                  enabled: false,
                                },
                                fontSize: 11,
                              }}
                            />
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    </Accordion>
                  </TabsContent>
                )}
              </div>
            </div>
          </form>
        </FormProvider>
      </Tabs>
    </div>
  )
}
function SaveStateIcon({ saveState }: { saveState: SaveState }) {
  return (
    <div
      className={cn(
        "absolute right-4 top-4 flex items-center justify-end space-x-2",
        "transition-all duration-300 ease-in-out",
        saveState === SaveState.IDLE && "opacity-0"
      )}
    >
      {saveState === SaveState.UNSAVED && (
        <>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="size-3 text-muted-foreground"
          >
            <path d="M13 13H8a1 1 0 0 0-1 1v7" />
            <path d="M14 8h1" />
            <path d="M17 21v-4" />
            <path d="m2 2 20 20" />
            <path d="M20.41 20.41A2 2 0 0 1 19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 .59-1.41" />
            <path d="M29.5 11.5s5 5 4 5" />
            <path d="M9 3h6.2a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V15" />
          </svg>
          <span className="text-xs text-muted-foreground">Unsaved</span>
          <SaveShortcut />
        </>
      )}
      {saveState === SaveState.SAVING && (
        <>
          <Loader2Icon className="size-3 animate-spin text-muted-foreground" />
          <span className="text-xs text-muted-foreground">Saving</span>
        </>
      )}
      {saveState === SaveState.SAVED && (
        <>
          <CircleCheckIcon className="size-4 fill-emerald-500 stroke-white" />
          <span className="text-xs text-emerald-600">Saved</span>
        </>
      )}
      {saveState === SaveState.ERROR && (
        <>
          <AlertTriangleIcon className="size-4 fill-rose-500 stroke-white" />
          <span className="text-xs text-rose-500">Error</span>
        </>
      )}
    </div>
  )
}

function SaveShortcut() {
  return (
    <span className="my-px ml-auto flex items-center space-x-2">
      <div className="mx-1 my-0 flex items-center space-x-1 rounded-sm border border-muted-foreground/20 bg-muted-foreground/10 px-px py-0 font-mono text-xs text-muted-foreground/80">
        <SaveIcon className="size-3 text-muted-foreground/70" />
        <p>
          {typeof navigator.userAgent !== "undefined"
            ? /Mac|iPod|iPhone|iPad/.test(navigator.userAgent)
              ? "⌘+s"
              : "ctrl+s"
            : "ctrl+s"}
        </p>
      </div>
    </span>
  )
}

export interface ActionPanelEmptyProps
  extends React.HTMLAttributes<HTMLDivElement> {
  title?: string
  description?: React.ReactNode | string
  action?: () => void
  icon?: LucideIcon
  actionLabel?: string
}

export function ActionPanelNotFound({
  title = "This action could not be found",
  description = "Please check the action type and try again.",
  action,
  actionLabel = "Create item",
  className,
  ...props
}: ActionPanelEmptyProps) {
  if (typeof description === "string") {
    description = <p className="text-xs text-muted-foreground">{description}</p>
  }
  return (
    <div
      id="outer"
      className={cn(
        "flex size-full flex-col items-center justify-center",
        className
      )}
      {...props}
    >
      <div className="flex flex-col items-center gap-4 p-6 text-center">
        <div className="size-12 rounded-full bg-muted p-3 font-semibold">?</div>
        <div className="space-y-1">
          <h4 className="text-sm font-semibold">{title}</h4>
          {description}
        </div>
        {action && (
          <Button
            variant="outline"
            size="sm"
            onClick={action}
            className="w-full gap-1.5"
          >
            <Plus className="size-4" />
            {actionLabel}
          </Button>
        )}
      </div>
    </div>
  )
}
