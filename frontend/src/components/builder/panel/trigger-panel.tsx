"use client"

import "react18-json-view/src/style.css"

import { zodResolver } from "@hookform/resolvers/zod"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  BanIcon,
  CalendarClockIcon,
  ClipboardListIcon,
  KeyRoundIcon,
  MoreHorizontalIcon,
  PlusCircleIcon,
  RotateCcwIcon,
  SquarePlay,
  Trash2Icon,
  WebhookIcon,
} from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import {
  ApiError,
  type CaseEventType,
  type SchedulesCreateScheduleData,
  type WebhookMethod,
  type WebhookRead,
  type WorkflowRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { TriggerTypename } from "@/components/builder/canvas/trigger-node"
import {
  type TriggerPanelTab,
  TriggerPanelTabs,
} from "@/components/builder/panel/trigger-panel-tabs"
import {
  CaseEventsField,
  CaseTagFiltersField,
} from "@/components/case-triggers/case-trigger-form-fields"
import { CopyButton } from "@/components/copy-button"
import { FormTriggerControls } from "@/components/forms/form-trigger-controls"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ReadOnlyFieldset } from "@/components/managed/read-only-fieldset"
import { AlertNotification } from "@/components/notifications"
import {
  ScheduleFormFields,
  type ScheduleInputs,
  scheduleInputsToSettings,
  useScheduleForm,
} from "@/components/schedules/schedule-form-fields"
import type { Tag } from "@/components/tags-input"
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
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  normalizeCidrTags,
  WebhookAllowlistHint,
  WebhookAllowlistInput,
  type WebhookForm,
  WebhookIncludeHeadersToggle,
  WebhookMethodsSelect,
  webhookFormSchema,
} from "@/components/webhooks/webhook-form-fields"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useFormsAccess } from "@/hooks/use-forms-access"
import {
  useCaseTagCatalog,
  useCaseTrigger,
  useDeleteWebhookApiKey,
  useGenerateWebhookApiKey,
  useRevokeWebhookApiKey,
  useSchedules,
  useUpdateWebhook,
  useUpsertCaseTrigger,
} from "@/lib/hooks"
import { durationToHumanReadable } from "@/lib/time"
import { cn } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

const extractApiErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof ApiError) {
    const body = error.body as Record<string, unknown> | undefined
    if (body) {
      const detail = body.detail
      if (typeof detail === "string") {
        return detail
      }
      const message = body.message
      if (typeof message === "string") {
        return message
      }
      const title = body.title
      if (typeof title === "string") {
        return title
      }
      const errors = body.errors
      if (Array.isArray(errors) && errors.length > 0) {
        const first = errors[0] as Record<string, unknown>
        const msg = first?.msg ?? first?.message ?? first?.detail
        if (typeof msg === "string") {
          return msg
        }
      }
    }
  }
  return fallback
}

const formatScheduleDate = (value?: string | null) => {
  if (!value) {
    return "None"
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return "Invalid date"
  }
  return parsed.toLocaleString()
}

export function TriggerPanel({ workflow }: { workflow: WorkflowRead }) {
  const { triggerPanelTab, setTriggerPanelTab } = useWorkflowBuilder()
  // Managed workflows keep the webhook switch, schedules and form switch.
  const { isReadOnly } = useWorkflow()
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")
  // The Form tab needs Forms on (flag and entitlement) and `form:read`.
  const formsAccess = useFormsAccess()
  const canReadForms = useScopeCheck("form:read")
  const formTabVisible = formsAccess === "enabled" && canReadForms === true

  return (
    <div className="overflow-auto size-full">
      <div className="grid grid-cols-3">
        <div className="overflow-hidden col-span-2">
          <h3 className="p-4">
            <div className="flex items-center space-x-4 w-full">
              {getIcon(TriggerTypename, {
                className: "size-10 p-2",
                flairsize: "md",
              })}
              <div className="flex flex-1 justify-between space-x-12 w-full">
                <div className="flex flex-col">
                  <div className="flex justify-between items-center w-full text-xs font-medium leading-none">
                    <div className="flex w-full">Trigger</div>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    Workflow Triggers
                  </p>
                </div>
              </div>
            </div>
          </h3>
        </div>
      </div>
      <Tabs
        value={triggerPanelTab}
        onValueChange={(value) => {
          setTriggerPanelTab(value as TriggerPanelTab)
        }}
        className="mt-1 flex h-full w-full flex-col"
      >
        <div className="w-full">
          {/* Four tabs can be wider than the panel. Scroll the strip itself so
              focusing the last tab does not shift the whole panel sideways. */}
          <div className="no-scrollbar flex items-center justify-start overflow-x-auto">
            <TabsList className="h-8 justify-start rounded-none bg-transparent p-0">
              <TabsTrigger
                className="flex h-full min-w-24 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value={TriggerPanelTabs.webhook}
              >
                <WebhookIcon className="mr-2 size-4" />
                <span>Webhook</span>
              </TabsTrigger>
              <TabsTrigger
                className="flex h-full min-w-24 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                value={TriggerPanelTabs.schedules}
              >
                <CalendarClockIcon className="mr-2 size-4" />
                <span>Schedules</span>
              </TabsTrigger>
              {caseAddonsEnabled && (
                <TabsTrigger
                  className="flex h-full min-w-24 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value={TriggerPanelTabs.caseTriggers}
                >
                  <SquarePlay className="mr-2 size-4" />
                  <span>Case triggers</span>
                </TabsTrigger>
              )}
              {formTabVisible && (
                <TabsTrigger
                  className="flex h-full min-w-24 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value={TriggerPanelTabs.form}
                >
                  <ClipboardListIcon className="mr-2 size-4" />
                  <span>Form</span>
                </TabsTrigger>
              )}
            </TabsList>
          </div>
          <Separator />
        </div>
        <div className="flex-1 overflow-auto">
          <TabsContent value={TriggerPanelTabs.webhook} className="pb-8">
            <div className="px-4 my-4 space-y-2">
              <WebhookControls
                webhook={workflow.webhook}
                workflowId={workflow.id}
                readOnly={isReadOnly}
              />
            </div>
          </TabsContent>
          <TabsContent value={TriggerPanelTabs.schedules} className="pb-8">
            <div className="px-4 my-4 space-y-2">
              <ScheduleControls workflowId={workflow.id} />
            </div>
          </TabsContent>
          {caseAddonsEnabled && (
            <TabsContent value={TriggerPanelTabs.caseTriggers} className="pb-8">
              <div className="px-4 my-4 space-y-2">
                <CaseTriggerControls
                  workflowId={workflow.id}
                  readOnly={isReadOnly}
                />
              </div>
            </TabsContent>
          )}
          {formTabVisible && (
            <TabsContent value={TriggerPanelTabs.form} className="pb-8">
              <div className="px-4 my-4 space-y-2">
                <FormTriggerControls workflow={workflow} />
              </div>
            </TabsContent>
          )}
        </div>
      </Tabs>
    </div>
  )
}

export function WebhookControls({
  webhook,
  workflowId,
  readOnly = false,
}: {
  webhook: WebhookRead
  workflowId: string
  /** Lock every webhook setting except the on/off switch. */
  readOnly?: boolean
}) {
  const hasActiveApiKey = webhook.api_key?.is_active ?? false
  const hasRevokedApiKey = Boolean(webhook.api_key && !hasActiveApiKey)
  const apiKeyPreview = webhook.api_key?.preview ?? null
  const apiKeyCreatedAt = webhook.api_key?.created_at ?? null
  const apiKeyLastUsedAt = webhook.api_key?.last_used_at ?? null
  const apiKeyRevokedAt = webhook.api_key?.revoked_at ?? null

  const workspaceId = useWorkspaceId()
  const { mutateAsync, isPending: isUpdatingWebhook } = useUpdateWebhook(
    workspaceId,
    workflowId
  )
  const { mutateAsync: generateWebhookApiKey, isPending: isGeneratingApiKey } =
    useGenerateWebhookApiKey(workspaceId, workflowId)
  const { mutateAsync: revokeWebhookApiKey, isPending: isRevokingApiKey } =
    useRevokeWebhookApiKey(workspaceId, workflowId)
  const { mutateAsync: deleteWebhookApiKey, isPending: isDeletingApiKey } =
    useDeleteWebhookApiKey(workspaceId, workflowId)
  const [generatedApiKey, setGeneratedApiKey] = useState<string | null>(null)
  const [generatedAt, setGeneratedAt] = useState<string | null>(null)
  const [apiKeyDialogOpen, setApiKeyDialogOpen] = useState(false)
  const [confirmRegenerateDialogOpen, setConfirmRegenerateDialogOpen] =
    useState(false)
  const [confirmRevokeDialogOpen, setConfirmRevokeDialogOpen] = useState(false)
  const [confirmDeleteDialogOpen, setConfirmDeleteDialogOpen] = useState(false)

  const form = useForm<WebhookForm>({
    resolver: zodResolver(webhookFormSchema),
    mode: "onChange",
    reValidateMode: "onChange",
    values: {
      status: webhook.status,
      methods: webhook.methods ?? ["POST"],
      allowlisted_cidrs:
        webhook.allowlisted_cidrs?.map((cidr) => ({
          id: cidr,
          text: cidr,
        })) ?? [],
      include_headers: webhook.include_headers ?? false,
    },
  })

  const handleAllowlistedCidrsChange = useCallback(
    async (newTags: Tag[] | undefined) => {
      if (!Array.isArray(newTags)) {
        form.setValue("allowlisted_cidrs", [], {
          shouldDirty: true,
          shouldValidate: true,
          shouldTouch: true,
        })
        return
      }

      const result = normalizeCidrTags(newTags)
      if ("error" in result) {
        form.setError("allowlisted_cidrs", {
          type: "manual",
          message: result.error,
        })
        toast({
          title: "Invalid IP allowlist entry",
          description: result.error,
          variant: "warning",
        })
        return
      }

      form.clearErrors("allowlisted_cidrs")
      const normalizedValues = result.tags
      form.setValue("allowlisted_cidrs", normalizedValues, {
        shouldDirty: true,
        shouldValidate: true,
        shouldTouch: true,
      })

      // Auto-save the allowlist
      const payload = normalizedValues.map((tag) => tag.text)
      try {
        await mutateAsync({ allowlisted_cidrs: payload })
        form.reset({ ...form.getValues(), allowlisted_cidrs: normalizedValues })
        toast({
          title: "Allowlist updated",
          description: "Webhook allowlist saved successfully.",
        })
      } catch (error) {
        const description = extractApiErrorMessage(
          error,
          "Failed to update the IP allowlist."
        )
        toast({
          title: "Failed to save allowlist",
          description,
          variant: "destructive",
        })
      }
    },
    [form, mutateAsync]
  )

  const formatTimestamp = (value: string | null) =>
    value ? new Date(value).toLocaleString() : "—"

  const handleStatusChange = async (checked: boolean) => {
    const newStatus = checked ? "online" : "offline"
    form.setValue("status", newStatus)

    try {
      await mutateAsync({ status: newStatus })
      toast({
        title: "Webhook status updated",
        description: `Webhook is now ${newStatus}`,
      })
    } catch (error) {
      console.error("Failed to update webhook status", error)
      form.setValue("status", webhook.status)
      toast({
        title: "Failed to update status",
        description: extractApiErrorMessage(
          error,
          "An error occurred while updating the webhook status."
        ),
      })
    }
  }

  const handleIncludeHeadersChange = async (checked: boolean) => {
    form.setValue("include_headers", checked)

    try {
      await mutateAsync({ include_headers: checked })
      toast({
        title: "Webhook trigger payload updated",
        description: checked
          ? "TRIGGER will include request headers and metadata"
          : "TRIGGER will receive the request body only",
      })
    } catch (error) {
      console.error("Failed to update webhook include_headers", error)
      // Restore the value from before this toggle, not the (possibly stale) prop
      form.setValue("include_headers", !checked)
      toast({
        title: "Failed to update trigger payload",
        description: extractApiErrorMessage(
          error,
          "An error occurred while updating the webhook trigger payload."
        ),
        variant: "destructive",
      })
    }
  }

  const handleMethodsChange = async (newMethods: WebhookMethod[]) => {
    if (newMethods.length === 0) {
      console.log("No methods selected")
      return
    }

    form.setValue("methods", newMethods)

    try {
      await mutateAsync({ methods: newMethods })
      toast({
        title: "Webhook methods updated",
        description: `The webhook will accept requests via: ${newMethods.sort().join(", ")}`,
      })
    } catch (error) {
      console.error("Failed to update webhook methods", error)
      form.setValue("methods", webhook.methods ?? ["POST"])
      toast({
        title: "Failed to update methods",
        description: extractApiErrorMessage(
          error,
          "An error occurred while updating the webhook methods."
        ),
        variant: "destructive",
      })
    }
  }

  const handleGenerateApiKey = async (): Promise<boolean> => {
    try {
      const response = await generateWebhookApiKey()
      setGeneratedApiKey(response.api_key)
      setGeneratedAt(response.created_at ?? null)
      setApiKeyDialogOpen(true)
      toast({
        title: "API key generated",
        description: "Copy the API key now. It will not be shown again.",
      })
      return true
    } catch (error) {
      console.error("Failed to generate webhook API key", error)
      toast({
        title: "Failed to generate API key",
        description: extractApiErrorMessage(
          error,
          "An error occurred while generating the key."
        ),
        variant: "destructive",
      })
      return false
    }
  }

  const handleDialogChange = (open: boolean) => {
    setApiKeyDialogOpen(open)
    if (!open) {
      setGeneratedApiKey(null)
      setGeneratedAt(null)
    }
  }

  const handleConfirmRegenerate = async () => {
    const didGenerate = await handleGenerateApiKey()
    if (didGenerate) {
      setConfirmRegenerateDialogOpen(false)
    }
  }

  const handleConfirmRevoke = async () => {
    try {
      await revokeWebhookApiKey()
      setConfirmRevokeDialogOpen(false)
    } catch (error) {
      console.error("Failed to revoke webhook API key", error)
    }
  }

  const handleConfirmDelete = async () => {
    try {
      await deleteWebhookApiKey()
      setConfirmDeleteDialogOpen(false)
    } catch (error) {
      console.error("Failed to delete webhook API key", error)
    }
  }

  return (
    <div className="space-y-4">
      <Form {...form}>
        <FormField
          control={form.control}
          name="status"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center justify-between gap-4 rounded-md border p-3">
                <div className="space-y-1">
                  <Label className="text-xs font-semibold">
                    Enable webhook
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    {field.value === "online"
                      ? "Webhook is currently active and receiving requests"
                      : "Webhook is disabled"}
                  </p>
                </div>
                <FormControl>
                  <Switch
                    checked={field.value === "online"}
                    onCheckedChange={handleStatusChange}
                    className="data-[state=checked]:bg-emerald-500"
                    disabled={isUpdatingWebhook}
                  />
                </FormControl>
              </div>
            </FormItem>
          )}
        />

        <ReadOnlyFieldset readOnly={readOnly} className="space-y-4">
          <FormField
            control={form.control}
            name="include_headers"
            render={({ field }) => (
              <FormItem>
                <FormControl>
                  <WebhookIncludeHeadersToggle
                    checked={field.value}
                    onCheckedChange={handleIncludeHeadersChange}
                    disabled={isUpdatingWebhook}
                  />
                </FormControl>
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="methods"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="flex gap-2 items-center text-xs font-medium">
                  <span>Allowed HTTP methods</span>
                </FormLabel>
                <FormControl>
                  <WebhookMethodsSelect
                    value={field.value}
                    onChange={handleMethodsChange}
                    disabled={isUpdatingWebhook}
                  />
                </FormControl>
                <FormMessage className="text-xs" />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="allowlisted_cidrs"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-xs font-medium">
                  <span>IP allowlist</span>
                </FormLabel>
                <FormControl>
                  <WebhookAllowlistInput
                    tags={field.value}
                    onTagsChange={handleAllowlistedCidrsChange}
                  />
                </FormControl>
                <FormMessage className="text-xs" />
                <FormDescription className="text-xs">
                  <WebhookAllowlistHint />
                </FormDescription>
              </FormItem>
            )}
          />
        </ReadOnlyFieldset>
      </Form>

      <div className="space-y-3">
        <Label className="flex items-center gap-2 text-xs font-medium">
          <span>API key</span>
        </Label>
        {hasActiveApiKey ? (
          <div className="rounded-lg border bg-muted/40 p-4 text-xs shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-3">
                <span className="text-xs uppercase tracking-wide text-muted-foreground">
                  Active key
                </span>
                <span className="font-mono text-xs tracking-wide">
                  {apiKeyPreview ?? "—"}
                </span>
              </div>
              <TooltipProvider>
                <Tooltip>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8 rounded-full text-muted-foreground hover:text-foreground"
                          disabled={
                            isGeneratingApiKey ||
                            isRevokingApiKey ||
                            isDeletingApiKey
                          }
                        >
                          <MoreHorizontalIcon className="size-4" />
                          <span className="sr-only">Manage API key</span>
                        </Button>
                      </TooltipTrigger>
                    </DropdownMenuTrigger>
                    <TooltipContent side="bottom" sideOffset={4}>
                      Manage API key
                    </TooltipContent>
                    <DropdownMenuContent align="end" sideOffset={4}>
                      <DropdownMenuItem
                        onSelect={(event) => {
                          event.preventDefault()
                          setConfirmRegenerateDialogOpen(true)
                        }}
                        disabled={
                          isGeneratingApiKey ||
                          isRevokingApiKey ||
                          isDeletingApiKey
                        }
                        className="flex items-center gap-2"
                      >
                        <RotateCcwIcon className="size-4" />
                        <span>Regenerate</span>
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={(event) => {
                          event.preventDefault()
                          setConfirmRevokeDialogOpen(true)
                        }}
                        disabled={
                          isGeneratingApiKey ||
                          isRevokingApiKey ||
                          isDeletingApiKey
                        }
                        className="flex items-center gap-2"
                      >
                        <BanIcon className="size-4" />
                        <span>Revoke</span>
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={(event) => {
                          event.preventDefault()
                          setConfirmDeleteDialogOpen(true)
                        }}
                        disabled={
                          isGeneratingApiKey ||
                          isRevokingApiKey ||
                          isDeletingApiKey
                        }
                        className="flex items-center gap-2 text-destructive focus:text-destructive"
                      >
                        <Trash2Icon className="size-4" />
                        <span>Delete</span>
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </Tooltip>
              </TooltipProvider>
            </div>
            <Separator className="my-3" />
            <dl className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Created
                </dt>
                <dd className="font-medium text-accent-foreground">
                  {formatTimestamp(apiKeyCreatedAt)}
                </dd>
              </div>
              <div className="space-y-1">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Last used
                </dt>
                <dd className="font-medium text-accent-foreground">
                  {formatTimestamp(apiKeyLastUsedAt)}
                </dd>
              </div>
            </dl>
          </div>
        ) : hasRevokedApiKey ? (
          <div className="rounded-lg border border-amber-300 bg-amber-50/70 p-4 text-xs shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <span className="text-xs uppercase tracking-wide text-amber-700">
                    API key revoked
                  </span>
                  <span className="font-mono text-xs tracking-wide text-amber-900">
                    {apiKeyPreview ?? "—"}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Revoked {formatTimestamp(apiKeyRevokedAt)}
                </p>
                <p className="text-[11px] text-amber-800">
                  Webhook requests are blocked until a new API key is generated.
                  Regenerate to restore access or delete to remove protection.
                </p>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setConfirmRegenerateDialogOpen(true)}
                  disabled={
                    isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                  }
                >
                  {isGeneratingApiKey ? "Generating..." : "Regenerate"}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => setConfirmDeleteDialogOpen(true)}
                  disabled={
                    isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                  }
                >
                  Delete
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed bg-muted/20 p-4 text-xs shadow-sm">
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">
                Webhook is not protected
              </p>
              <p className="text-xs text-muted-foreground">
                No API key is configured. Generate an API key to require clients
                to authenticate webhook requests.
              </p>
            </div>
            <Button
              size="sm"
              variant="secondary"
              className="mt-3 w-full justify-center"
              onClick={handleGenerateApiKey}
              disabled={
                isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
              }
            >
              <>
                <KeyRoundIcon
                  className={cn(
                    "mr-2 h-4 w-4",
                    isGeneratingApiKey
                      ? "animate-spin text-muted-foreground"
                      : ""
                  )}
                />
                {isGeneratingApiKey ? "Generating..." : "Generate API key"}
              </>
            </Button>
          </div>
        )}
        <AlertDialog
          open={confirmRegenerateDialogOpen}
          onOpenChange={setConfirmRegenerateDialogOpen}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Regenerate API key?</AlertDialogTitle>
              <AlertDialogDescription>
                Rotating the API key immediately revokes the existing key.
                Clients must be updated to use the new key.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel
                disabled={
                  isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                }
              >
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                onClick={handleConfirmRegenerate}
                disabled={
                  isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                }
              >
                {isGeneratingApiKey ? "Generating..." : "Regenerate"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <AlertDialog
          open={confirmRevokeDialogOpen}
          onOpenChange={setConfirmRevokeDialogOpen}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Revoke API key?</AlertDialogTitle>
              <AlertDialogDescription>
                Revoking disables the existing key immediately while keeping an
                audit trail. Clients must use a newly generated key to
                authenticate.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel
                disabled={
                  isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                }
              >
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                onClick={handleConfirmRevoke}
                disabled={
                  isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                }
              >
                {isRevokingApiKey ? "Revoking..." : "Revoke"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <AlertDialog
          open={confirmDeleteDialogOpen}
          onOpenChange={setConfirmDeleteDialogOpen}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete API key?</AlertDialogTitle>
              <AlertDialogDescription>
                This permanently removes the API key. The webhook will no longer
                require authenticated requests until a new API key is generated.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel
                disabled={
                  isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                }
              >
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                onClick={handleConfirmDelete}
                disabled={
                  isGeneratingApiKey || isRevokingApiKey || isDeletingApiKey
                }
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              >
                {isDeletingApiKey ? "Deleting..." : "Delete"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <p className="text-xs text-muted-foreground">
          Webhook senders must pass the key in the x-tracecat-api-key header.
        </p>
      </div>

      <Dialog open={apiKeyDialogOpen} onOpenChange={handleDialogChange}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Webhook API key</DialogTitle>
            <DialogDescription>
              Copy the key now. It will not be shown again.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="space-y-3 text-xs">
              <div className="flex justify-between text-muted-foreground">
                <span>Generated</span>
                <span>{formatTimestamp(generatedAt)}</span>
              </div>
              <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
                <code className="break-all text-xs">
                  {generatedApiKey ?? "—"}
                </code>
                {generatedApiKey ? (
                  <CopyButton
                    value={generatedApiKey}
                    toastMessage="Copied API key to clipboard"
                  />
                ) : null}
              </div>
              <p className="text-xs text-muted-foreground">
                Rotate the API key if it is ever exposed.
              </p>
            </div>
          </DialogBody>
        </DialogContent>
      </Dialog>

      <div className="space-y-2">
        <Label className="flex gap-2 items-center text-xs font-medium">
          <span>URL</span>
          <CopyButton
            value={webhook.url}
            toastMessage="Copied URL to clipboard"
          />
        </Label>
        <div className="rounded-md border shadow-sm">
          <Input
            name="url"
            defaultValue={webhook.url}
            className="text-xs rounded-md border-none shadow-none"
            readOnly
            disabled
          />
        </div>
      </div>
    </div>
  )
}

export function CaseTriggerControls({
  workflowId,
  readOnly = false,
}: {
  workflowId: string
  /** Lock case trigger settings on managed workflows. */
  readOnly?: boolean
}) {
  const workspaceId = useWorkspaceId()
  const {
    data: caseTrigger,
    isLoading: isLoadingCaseTrigger,
    error: caseTriggerError,
  } = useCaseTrigger(workspaceId, workflowId)
  const { mutateAsync: upsertCaseTrigger, isPending: isUpdatingCaseTrigger } =
    useUpsertCaseTrigger(workspaceId, workflowId)
  // Start the tag catalog with the case trigger, not after it loads: the tag
  // field reads the same cached query once it mounts.
  useCaseTagCatalog(workspaceId)

  const [status, setStatus] = useState<"online" | "offline">("offline")
  const [eventTypes, setEventTypes] = useState<CaseEventType[]>([])
  const [tagFilters, setTagFilters] = useState<string[]>([])

  useEffect(() => {
    if (!caseTrigger) {
      return
    }
    setStatus(caseTrigger.status)
    setEventTypes(caseTrigger.event_types ?? [])
    setTagFilters(caseTrigger.tag_filters ?? [])
  }, [caseTrigger])

  const persist = useCallback(
    async (
      nextStatus: "online" | "offline",
      nextEvents: CaseEventType[],
      nextTags: string[]
    ) => {
      await upsertCaseTrigger({
        status: nextStatus,
        event_types: nextEvents,
        tag_filters: nextTags,
      })
    },
    [upsertCaseTrigger]
  )

  const handleStatusToggle = useCallback(
    async (checked: boolean) => {
      const nextStatus = checked ? "online" : "offline"
      if (nextStatus === "online" && eventTypes.length === 0) {
        toast({
          title: "Select case events",
          description: "Choose at least one case event before enabling.",
          variant: "warning",
        })
        return
      }
      const previousStatus = status
      setStatus(nextStatus)
      try {
        await persist(nextStatus, eventTypes, tagFilters)
        toast({
          title: "Case trigger updated",
          description: `Case triggers are now ${nextStatus}.`,
        })
      } catch {
        setStatus(previousStatus)
      }
    },
    [eventTypes, persist, status, tagFilters]
  )

  const handleEventTypesChange = useCallback(
    async (nextEvents: CaseEventType[]) => {
      const wasOnline = status === "online"
      const nextStatus =
        wasOnline && nextEvents.length === 0 ? "offline" : status

      const previousEvents = eventTypes
      const previousStatus = status
      setEventTypes(nextEvents)
      if (nextStatus !== status) {
        setStatus(nextStatus)
      }

      try {
        await persist(nextStatus, nextEvents, tagFilters)
        if (wasOnline && nextEvents.length === 0) {
          toast({
            title: "Case triggers disabled",
            description: "Select an event type to re-enable case triggers.",
          })
        } else {
          toast({
            title: "Case events updated",
            description: "Case trigger events saved successfully.",
          })
        }
      } catch {
        setEventTypes(previousEvents)
        setStatus(previousStatus)
      }
    },
    [eventTypes, persist, status, tagFilters]
  )

  const handleTagFiltersChange = useCallback(
    async (values: string[]) => {
      const previousFilters = tagFilters
      setTagFilters(values)
      try {
        await persist(status, eventTypes, values)
        toast({
          title: "Tag allowlist updated",
          description: "Case trigger tag filters saved successfully.",
        })
      } catch {
        setTagFilters(previousFilters)
      }
    },
    [eventTypes, persist, status, tagFilters]
  )

  if (isLoadingCaseTrigger) {
    return <CenteredSpinner />
  }

  if (caseTriggerError) {
    return (
      <AlertNotification
        level="error"
        message="Failed to load case trigger configuration."
      />
    )
  }

  return (
    <ReadOnlyFieldset readOnly={readOnly} className="space-y-4">
      <div className="flex items-center justify-between gap-4 rounded-md border p-3">
        <div className="space-y-1">
          <Label className="text-xs font-semibold">Enable case triggers</Label>
          <p className="text-xs text-muted-foreground">
            Trigger workflows when selected case events occur.
          </p>
        </div>
        <Switch
          checked={status === "online"}
          onCheckedChange={handleStatusToggle}
          disabled={isUpdatingCaseTrigger}
        />
      </div>

      <CaseEventsField
        value={eventTypes}
        onChange={handleEventTypesChange}
        disabled={isUpdatingCaseTrigger}
      />

      <CaseTagFiltersField
        value={tagFilters}
        onChange={handleTagFiltersChange}
        disabled={isUpdatingCaseTrigger}
      />
    </ReadOnlyFieldset>
  )
}

export function ScheduleControls({ workflowId }: { workflowId: string }) {
  const {
    schedules,
    schedulesIsLoading,
    schedulesError,
    updateSchedule,
    deleteSchedule,
  } = useSchedules(workflowId)
  const workspaceId = useWorkspaceId()

  if (schedulesIsLoading) {
    return <CenteredSpinner />
  }
  if (schedulesError || !schedules) {
    return (
      <AlertNotification level="error" message="Failed to load schedules." />
    )
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-3 text-xs font-semibold">ID</TableHead>
            <TableHead className="text-xs font-semibold">Type</TableHead>
            <TableHead className="text-xs font-semibold">Schedule</TableHead>
            <TableHead className="text-xs font-semibold">Status</TableHead>
            <TableHead className="text-xs font-semibold">Timeout</TableHead>
            <TableHead className="text-xs font-semibold">Offset</TableHead>
            <TableHead className="text-xs font-semibold">Starts</TableHead>
            <TableHead className="text-xs font-semibold">Ends</TableHead>
            <TableHead className="text-right text-xs font-semibold">
              Actions
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {schedules.length > 0 ? (
            schedules.map(
              ({
                id,
                status,
                every,
                cron,
                timeout,
                offset,
                start_at,
                end_at,
              }) => {
                const isCron = Boolean(cron)
                const scheduleLabel = isCron
                  ? cron
                  : every
                    ? durationToHumanReadable(every)
                    : "—"
                const offsetLabel =
                  !isCron && offset
                    ? (() => {
                        try {
                          return durationToHumanReadable(offset)
                        } catch {
                          return offset
                        }
                      })()
                    : "None"
                const startLabel = formatScheduleDate(start_at)
                const endLabel = formatScheduleDate(end_at)

                return (
                  <TableRow key={id} className="ext-xs text-muted-foreground">
                    <TableCell className="items-center pl-3 text-xs">
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              className="font-mono text-xs hover:text-foreground transition-colors cursor-pointer"
                              onClick={async () => {
                                await navigator.clipboard.writeText(id!)
                                toast({
                                  title: "Copied to clipboard",
                                  description:
                                    "Schedule ID copied successfully",
                                })
                              }}
                            >
                              {id?.slice(0, 8)}...
                            </button>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p className="font-mono text-xs">{id}</p>
                            <p className="text-xs text-muted-foreground">
                              Click to copy
                            </p>
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </TableCell>
                    <TableCell className="items-center text-xs">
                      {isCron ? "Cron" : "Interval"}
                    </TableCell>
                    <TableCell className="items-center text-xs">
                      {isCron ? (
                        <code className="rounded bg-muted px-1 py-0.5 text-xs">
                          {scheduleLabel}
                        </code>
                      ) : (
                        scheduleLabel
                      )}
                    </TableCell>
                    <TableCell className="text-xs capitalize">
                      <div className="flex">
                        <p>{status}</p>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs capitalize">
                      <div className="flex">
                        <p>{timeout ? `${timeout}s` : "None"}</p>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="flex">
                        <p>{offsetLabel}</p>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="flex">
                        <p>{startLabel}</p>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="flex">
                        <p>{endLabel}</p>
                      </div>
                    </TableCell>
                    <TableCell className="items-center pr-3 text-xs">
                      <div className="flex justify-end">
                        <AlertDialog>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button className="p-0 size-6" variant="ghost">
                                <DotsHorizontalIcon className="size-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent>
                              <DropdownMenuLabel className="text-xs">
                                Actions
                              </DropdownMenuLabel>
                              <DropdownMenuItem
                                onClick={() =>
                                  id && navigator.clipboard.writeText(id)
                                }
                                className="text-xs"
                              >
                                Copy ID
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                className={cn("text-xs", status === "online")}
                                onClick={async () => {
                                  if (!id) return
                                  await updateSchedule({
                                    workspaceId,
                                    scheduleId: id,
                                    requestBody: {
                                      status:
                                        status === "online"
                                          ? "offline"
                                          : "online",
                                    },
                                  })
                                }}
                              >
                                {status === "online" ? "Pause" : "Unpause"}
                              </DropdownMenuItem>
                              <AlertDialogTrigger asChild>
                                <DropdownMenuItem className="text-xs text-rose-500 focus:text-rose-600">
                                  Delete
                                </DropdownMenuItem>
                              </AlertDialogTrigger>
                            </DropdownMenuContent>
                          </DropdownMenu>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>
                                Delete schedule
                              </AlertDialogTitle>
                              <AlertDialogDescription>
                                Are you sure you want to delete this schedule?
                                This action cannot be undone.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction
                                variant="destructive"
                                onClick={async () => {
                                  if (!id) return
                                  await deleteSchedule({
                                    workspaceId,
                                    scheduleId: id,
                                  })
                                }}
                              >
                                Confirm
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              }
            )
          ) : (
            <TableRow className="justify-center text-xs text-muted-foreground">
              <TableCell
                className="h-8 text-center bg-muted-foreground/5"
                colSpan={9}
              >
                No Schedules
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      <Separator />
      <CreateScheduleDialog workflowId={workflowId} />
    </div>
  )
}

export function CreateScheduleDialog({ workflowId }: { workflowId: string }) {
  const { createSchedule } = useSchedules(workflowId)
  const workspaceId = useWorkspaceId()
  const { workflow } = useWorkflow()
  const hasVersion = !!workflow?.version
  const [dialogOpen, setDialogOpen] = useState(false)
  const form = useScheduleForm()

  const onSubmit = async (values: ScheduleInputs) => {
    if (!hasVersion) {
      toast({
        title: "Cannot create schedule",
        description: "You must commit the workflow before creating a schedule.",
        variant: "warning",
      })
      return
    }

    try {
      const payload: SchedulesCreateScheduleData["requestBody"] = {
        workflow_id: workflowId,
        ...scheduleInputsToSettings(values),
      }

      const response = await createSchedule({
        workspaceId,
        requestBody: payload,
      })
      console.log("Schedule created", response)
      form.reset()
      setDialogOpen(false)
    } catch (error) {
      if (error instanceof ApiError) {
        console.error("Failed to create schedule", error.body)
      } else {
        console.error("Unexpected error when creating schedule", error)
      }
    }
  }

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      <TooltipProvider>
        <Tooltip open={!hasVersion ? undefined : false}>
          <TooltipTrigger asChild>
            <span>
              <DialogTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="flex gap-2 justify-center items-center w-full h-7 text-muted-foreground"
                  disabled={!hasVersion}
                >
                  <PlusCircleIcon className="size-4" />
                  <span>Create schedule</span>
                </Button>
              </DialogTrigger>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <p>You must save the workflow before creating a schedule.</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <DialogContent size="lg" className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Create schedule</DialogTitle>
          <DialogDescription>
            Configure the schedule for the workflow. The workflow will not run
            immediately.
            {!hasVersion && (
              <p className="mt-2 text-rose-500">
                Warning: You must commit the workflow before creating a
                schedule.
              </p>
            )}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={form.handleSubmit(onSubmit, () => {
              console.error("Form validation failed")
              toast({
                title: "Invalid inputs in form",
                description: "Please check the form for errors.",
              })
            })}
          >
            <DialogBody>
              <ScheduleFormFields form={form} />
            </DialogBody>
            <DialogFooter>
              <Button size="sm" type="submit" variant="default">
                <PlusCircleIcon className="mr-2 size-4" />
                <span>Create schedule</span>
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
