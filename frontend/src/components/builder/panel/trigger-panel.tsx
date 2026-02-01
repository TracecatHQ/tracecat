"use client"

import "react18-json-view/src/style.css"

import { zodResolver } from "@hookform/resolvers/zod"
import { CheckIcon, DotsHorizontalIcon } from "@radix-ui/react-icons"
import * as ipaddr from "ipaddr.js"
import {
  ActivityIcon,
  BanIcon,
  CalendarClockIcon,
  ChevronDownIcon,
  KeyRoundIcon,
  MoreHorizontalIcon,
  PlusCircleIcon,
  RotateCcwIcon,
  Trash2Icon,
  WebhookIcon,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  $WebhookMethod,
  $WebhookStatus,
  ApiError,
  type CaseEventType,
  type SchedulesCreateScheduleData,
  type WebhookMethod,
  type WebhookRead,
  type WorkflowRead,
} from "@/client"
import { TriggerTypename } from "@/components/builder/canvas/trigger-node"
import { CopyButton } from "@/components/copy-button"
import { getIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  CustomTagInput,
  type Suggestion,
  type Tag,
  MultiTagCommandInput,
} from "@/components/tags-input"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
import { Calendar } from "@/components/ui/calendar"
import {
  Dialog,
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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  useDeleteWebhookApiKey,
  useGenerateWebhookApiKey,
  useRevokeWebhookApiKey,
  useSchedules,
  useCaseTagCatalog,
  useCaseTrigger,
  useUpsertCaseTrigger,
  useUpdateWebhook,
} from "@/lib/hooks"
import {
  durationSchema,
  durationToHumanReadable,
  durationToISOString,
} from "@/lib/time"
import { cn } from "@/lib/utils"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

const HTTP_METHODS: readonly WebhookMethod[] = $WebhookMethod.enum

const CASE_EVENT_SUGGESTIONS: Suggestion[] = [
  {
    id: "case_created",
    label: "Case created",
    value: "case_created",
    group: "Case",
  },
  {
    id: "case_updated",
    label: "Case updated",
    value: "case_updated",
    group: "Case",
  },
  {
    id: "case_closed",
    label: "Case closed",
    value: "case_closed",
    group: "Case",
  },
  {
    id: "case_reopened",
    label: "Case reopened",
    value: "case_reopened",
    group: "Case",
  },
  {
    id: "case_viewed",
    label: "Case viewed",
    value: "case_viewed",
    group: "Case",
  },
  {
    id: "status_changed",
    label: "Status changed",
    value: "status_changed",
    group: "Fields",
  },
  {
    id: "priority_changed",
    label: "Priority changed",
    value: "priority_changed",
    group: "Fields",
  },
  {
    id: "severity_changed",
    label: "Severity changed",
    value: "severity_changed",
    group: "Fields",
  },
  {
    id: "fields_changed",
    label: "Fields changed",
    value: "fields_changed",
    group: "Fields",
  },
  {
    id: "assignee_changed",
    label: "Assignee changed",
    value: "assignee_changed",
    group: "Fields",
  },
  {
    id: "payload_changed",
    label: "Payload changed",
    value: "payload_changed",
    group: "Fields",
  },
  {
    id: "attachment_created",
    label: "Attachment added",
    value: "attachment_created",
    group: "Attachments",
  },
  {
    id: "attachment_deleted",
    label: "Attachment removed",
    value: "attachment_deleted",
    group: "Attachments",
  },
  {
    id: "tag_added",
    label: "Tag added",
    value: "tag_added",
    group: "Tags",
  },
  {
    id: "tag_removed",
    label: "Tag removed",
    value: "tag_removed",
    group: "Tags",
  },
  {
    id: "task_created",
    label: "Task created",
    value: "task_created",
    group: "Tasks",
  },
  {
    id: "task_deleted",
    label: "Task deleted",
    value: "task_deleted",
    group: "Tasks",
  },
  {
    id: "task_status_changed",
    label: "Task status changed",
    value: "task_status_changed",
    group: "Tasks",
  },
  {
    id: "task_priority_changed",
    label: "Task priority changed",
    value: "task_priority_changed",
    group: "Tasks",
  },
  {
    id: "task_workflow_changed",
    label: "Task workflow changed",
    value: "task_workflow_changed",
    group: "Tasks",
  },
  {
    id: "task_assignee_changed",
    label: "Task assignee changed",
    value: "task_assignee_changed",
    group: "Tasks",
  },
  {
    id: "dropdown_value_changed",
    label: "Dropdown value changed",
    value: "dropdown_value_changed",
    group: "Dropdowns",
  },
]

const toCanonicalString = (address: ipaddr.IPv4 | ipaddr.IPv6): string => {
  const maybeNormalized =
    (address as ipaddr.IPv6).toNormalizedString?.() ?? address.toString()
  return maybeNormalized
}

const validateAndNormalizeCidr = (
  value: string
): { normalized: string } | { error: string } => {
  try {
    const [address, prefixLength] = ipaddr.parseCIDR(value)
    if (address.kind() !== "ipv4") {
      throw new Error("Only IPv4 CIDR ranges are supported")
    }
    const normalized = `${toCanonicalString(address)}/${prefixLength}`
    return { normalized }
  } catch {
    try {
      const address = ipaddr.parse(value)
      if (address.kind() !== "ipv4") {
        throw new Error("Only IPv4 addresses are supported")
      }
      const prefixLength = 32
      const normalized = `${toCanonicalString(address)}/${prefixLength}`
      return { normalized }
    } catch {
      return {
        error: `Invalid IPv4 address or CIDR: "${value}"`,
      }
    }
  }
}

const webhookFormSchema = z.object({
  status: z.enum($WebhookStatus.enum),
  methods: z
    .array(z.enum($WebhookMethod.enum))
    .min(1, "At least one method is required"),
  allowlisted_cidrs: z
    .array(
      z.object({
        id: z.string(),
        text: z.string().trim().min(1, "Invalid IP address or CIDR"),
      })
    )
    .superRefine((cidrs, ctx) => {
      for (const cidr of cidrs) {
        const result = validateAndNormalizeCidr(cidr.text)
        if ("error" in result) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: result.error,
          })
          return
        }
      }
    })
    .transform((cidrs) => {
      const normalized = new Map<string, Tag>()
      for (const cidr of cidrs) {
        const result = validateAndNormalizeCidr(cidr.text)
        if ("error" in result) {
          continue
        }
        const value = result.normalized
        if (!normalized.has(value)) {
          normalized.set(value, { id: value, text: value })
        }
      }
      return Array.from(normalized.values())
    })
    .default([]),
})

type WebhookForm = z.infer<typeof webhookFormSchema>

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
      <Separator />
      {/* Metadata */}
      <Accordion
        type="multiple"
        defaultValue={[
          "trigger-settings",
          "trigger-webhooks",
          "trigger-case-triggers",
          "trigger-schedules",
        ]}
      >
        {/* Webhooks */}
        <AccordionItem value="trigger-webhooks" id="trigger-webhooks">
          <AccordionTrigger className="px-4 text-xs font-bold">
            <div className="flex items-center">
              <WebhookIcon className="mr-3 size-4" />
              <span>Webhook</span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="px-4 my-4 space-y-2">
              <WebhookControls
                webhook={workflow.webhook}
                workflowId={workflow.id}
              />
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Schedules */}
        <AccordionItem value="trigger-schedules" id="trigger-schedules">
          <AccordionTrigger className="px-4 text-xs font-bold">
            <div className="flex items-center">
              <CalendarClockIcon className="mr-3 size-4" />
              <span>Schedules</span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="px-4 my-4 space-y-2">
              <ScheduleControls workflowId={workflow.id} />
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Case Triggers */}
        <AccordionItem
          value="trigger-case-triggers"
          id="trigger-case-triggers"
        >
          <AccordionTrigger className="px-4 text-xs font-bold">
            <div className="flex items-center">
              <ActivityIcon className="mr-3 size-4" />
              <span>Case triggers</span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="px-4 my-4 space-y-2">
              <CaseTriggerControls workflowId={workflow.id} />
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  )
}

export function WebhookControls({
  webhook,
  workflowId,
}: {
  webhook: WebhookRead
  workflowId: string
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

      const normalized = new Map<string, Tag>()
      let firstError: string | null = null

      for (const tag of newTags) {
        const trimmed = tag.text.trim()
        if (!trimmed) {
          firstError ??= "Invalid IP address or CIDR"
          continue
        }
        const result = validateAndNormalizeCidr(trimmed)
        if ("error" in result) {
          firstError ??= result.error
          continue
        }
        const canonical = result.normalized
        if (!normalized.has(canonical)) {
          normalized.set(canonical, { id: canonical, text: canonical })
        }
      }

      if (firstError) {
        form.setError("allowlisted_cidrs", {
          type: "manual",
          message: firstError,
        })
        toast({
          title: "Invalid IP allowlist entry",
          description: firstError,
          variant: "destructive",
        })
        return
      }

      form.clearErrors("allowlisted_cidrs")
      const normalizedValues = Array.from(normalized.values())
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
              <div className="flex justify-between items-center">
                <FormLabel className="flex gap-2 items-center text-xs font-medium">
                  <span>Toggle Webhook</span>
                </FormLabel>
                <FormControl>
                  <Switch
                    checked={field.value === "online"}
                    onCheckedChange={handleStatusChange}
                    className="data-[state=checked]:bg-emerald-500"
                    disabled={isUpdatingWebhook}
                  />
                </FormControl>
              </div>
              <FormDescription className="text-xs">
                {field.value === "online"
                  ? "Webhook is currently active and receiving requests"
                  : "Webhook is disabled"}
              </FormDescription>
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="methods"
          render={({ field }) => (
            <FormItem>
              <FormLabel className="flex gap-2 items-center text-xs font-medium">
                <span>Allowed HTTP Methods</span>
              </FormLabel>
              <FormControl>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="outline"
                      className="justify-between w-full text-xs"
                      disabled={isUpdatingWebhook}
                    >
                      {field.value.length > 0
                        ? field.value.sort().join(", ")
                        : "Select HTTP methods"}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    style={{
                      width: "var(--radix-dropdown-menu-trigger-width)",
                    }}
                    align="start"
                    sideOffset={4}
                  >
                    {HTTP_METHODS.map((method) => (
                      <DropdownMenuItem
                        key={method}
                        onClick={() => {
                          const newMethods = field.value.includes(method)
                            ? field.value.filter((m) => m !== method)
                            : [...field.value, method]

                          handleMethodsChange(newMethods)
                        }}
                        className="w-full text-xs"
                      >
                        <CheckIcon
                          className={cn(
                            "mr-2 size-4",
                            field.value.includes(method)
                              ? "opacity-100"
                              : "opacity-0"
                          )}
                        />
                        <span>{method}</span>
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
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
                <span>IP Allowlist</span>
              </FormLabel>
              <FormControl>
                <CustomTagInput
                  {...field}
                  placeholder="Enter an IP address or CIDR. Allow all IPs by default."
                  tags={field.value}
                  setTags={(newTags) =>
                    handleAllowlistedCidrsChange(
                      Array.isArray(newTags) ? newTags : []
                    )
                  }
                />
              </FormControl>
              <FormMessage className="text-xs" />
              <FormDescription className="text-xs">
                Press{" "}
                <kbd className="px-1 py-0.5 text-[10px] font-semibold bg-muted border rounded tracking-tighter">
                  Enter ↵
                </kbd>{" "}
                to add a new entry.
              </FormDescription>
            </FormItem>
          )}
        />
      </Form>

      <div className="space-y-3">
        <Label className="flex items-center gap-2 text-xs font-medium">
          <span>API Key</span>
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
                {isDeletingApiKey ? "Deleting..." : "Delete key"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <p className="text-xs text-muted-foreground">
          Webhook senders must pass the key in the x-tracecat-api-key header.
        </p>
      </div>

      <Dialog open={apiKeyDialogOpen} onOpenChange={handleDialogChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Webhook API key</DialogTitle>
            <DialogDescription>
              Copy the key now. It will not be shown again.
            </DialogDescription>
          </DialogHeader>
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
          <DialogFooter>
            <Button onClick={() => handleDialogChange(false)}>Done</Button>
          </DialogFooter>
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

export function CaseTriggerControls({ workflowId }: { workflowId: string }) {
  const workspaceId = useWorkspaceId()
  const {
    data: caseTrigger,
    isLoading: isLoadingCaseTrigger,
    error: caseTriggerError,
  } = useCaseTrigger(workspaceId, workflowId)
  const { mutateAsync: upsertCaseTrigger, isPending: isUpdatingCaseTrigger } =
    useUpsertCaseTrigger(workspaceId, workflowId)
  const { caseTags, caseTagsIsLoading } = useCaseTagCatalog(workspaceId)

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
    async (nextStatus: "online" | "offline", nextEvents: CaseEventType[], nextTags: string[]) => {
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
          variant: "destructive",
        })
        return
      }
      setStatus(nextStatus)
      await persist(nextStatus, eventTypes, tagFilters)
      toast({
        title: "Case trigger updated",
        description: `Case triggers are now ${nextStatus}.`,
      })
    },
    [eventTypes, persist, tagFilters]
  )

  const handleEventTypesChange = useCallback(
    async (values: string[]) => {
      const nextEvents = values as CaseEventType[]
      const wasOnline = status === "online"
      const nextStatus =
        wasOnline && nextEvents.length === 0 ? "offline" : status

      setEventTypes(nextEvents)
      if (nextStatus !== status) {
        setStatus(nextStatus)
      }

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
    },
    [persist, status, tagFilters]
  )

  const handleTagFiltersChange = useCallback(
    async (values: string[]) => {
      setTagFilters(values)
      await persist(status, eventTypes, values)
      toast({
        title: "Tag allowlist updated",
        description: "Case trigger tag filters saved successfully.",
      })
    },
    [eventTypes, persist, status]
  )

  const tagSuggestions = useMemo(() => {
    return (caseTags ?? []).map((tag) => ({
      id: tag.id,
      label: tag.name,
      value: tag.ref,
      description: tag.ref,
    }))
  }, [caseTags])

  if (isLoadingCaseTrigger) {
    return <CenteredSpinner />
  }

  if (caseTriggerError) {
    return (
      <AlertNotification
        variant="destructive"
        title="Failed to load case triggers"
        description="We couldn't load the case trigger configuration."
      />
    )
  }

  return (
    <div className="space-y-4">
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

      <div className="space-y-2">
        <Label className="text-xs">Case events</Label>
        <MultiTagCommandInput
          value={eventTypes}
          onChange={handleEventTypesChange}
          suggestions={CASE_EVENT_SUGGESTIONS}
          searchKeys={["label", "value", "group"]}
          placeholder="Select case events..."
          disabled={isUpdatingCaseTrigger}
          allowCustomTags={false}
        />
        <p className="text-xs text-muted-foreground">
          Choose which case events will fire this workflow.
        </p>
      </div>

      <div className="space-y-2">
        <Label className="text-xs">Tag allowlist</Label>
        <MultiTagCommandInput
          value={tagFilters}
          onChange={handleTagFiltersChange}
          suggestions={tagSuggestions}
          searchKeys={["label", "value", "description"]}
          placeholder={
            caseTagsIsLoading ? "Loading tags..." : "Select case tags..."
          }
          disabled={isUpdatingCaseTrigger || caseTagsIsLoading}
          allowCustomTags={false}
        />
        <p className="text-xs text-muted-foreground">
          If empty, no tag filtering is applied.
        </p>
        <p className="text-xs text-muted-foreground">
          Uses current case tags; workflow-originated events are ignored.
        </p>
      </div>
    </div>
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
      <AlertNotification
        title="Failed to load schedules"
        message="There was an error when loading schedules."
      />
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

const ISO_8601_DURATION_REGEX =
  /^P(?!$)(\d+(?:\.\d+)?Y)?(\d+(?:\.\d+)?M)?(\d+(?:\.\d+)?W)?(\d+(?:\.\d+)?D)?(T(\d+(?:\.\d+)?H)?(\d+(?:\.\d+)?M)?(\d+(?:\.\d+)?S)?)?$/

const BASIC_CRON_REGEX = /^(\S+\s+){4,5}\S+$/

const durationNumber = z.coerce.number().int().nonnegative().catch(0)

const rawDurationSchema = z.object({
  years: durationNumber,
  months: durationNumber,
  weeks: durationNumber,
  days: durationNumber,
  hours: durationNumber,
  minutes: durationNumber,
  seconds: durationNumber,
})

const scheduleInputsSchema = z
  .object({
    mode: z.enum(["interval", "cron"]).default("interval"),
    duration: rawDurationSchema,
    cronExpression: z.string().optional(),
    timeout: z.number().optional(),
    offset: z.string().optional(),
    startAt: z.string().optional(),
    endAt: z.string().optional(),
  })
  .superRefine((values, ctx) => {
    if (values.mode === "interval") {
      try {
        // Validates at least one component and positive numbers
        durationSchema.parse(values.duration)
      } catch (error) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["duration.seconds"],
          message:
            error instanceof Error
              ? error.message
              : "Please provide a valid interval duration.",
        })
      }

      if (values.offset && values.offset.trim() !== "") {
        if (!ISO_8601_DURATION_REGEX.test(values.offset)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["offset"],
            message:
              "Must be a valid ISO 8601 duration string (e.g., PT1H, P1D, PT30M)",
          })
        }
      }
    } else {
      const cron = values.cronExpression?.trim()
      if (!cron) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["cronExpression"],
          message: "Cron expression is required for cron schedules.",
        })
      } else if (!BASIC_CRON_REGEX.test(cron)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["cronExpression"],
          message: "Enter a valid cron expression with 5 or 6 fields.",
        })
      }

      if (values.offset) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["offset"],
          message: "Offset is only supported for interval schedules.",
        })
      }
    }

    const startAt = values.startAt?.trim()
    const endAt = values.endAt?.trim()

    const parseDate = (value: string) => {
      const parsed = Date.parse(value)
      return Number.isNaN(parsed) ? null : parsed
    }

    if (startAt) {
      if (parseDate(startAt) === null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["startAt"],
          message: "Enter a valid start date and time.",
        })
      }
    }

    if (endAt) {
      if (parseDate(endAt) === null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["endAt"],
          message: "Enter a valid end date and time.",
        })
      }
    }

    if (startAt && endAt) {
      const start = parseDate(startAt)
      const end = parseDate(endAt)
      if (start !== null && end !== null && start > end) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["endAt"],
          message: "End time must be after the start time.",
        })
      }
    }
  })
type DurationType =
  | "duration.years"
  | "duration.months"
  | "duration.days"
  | "duration.hours"
  | "duration.minutes"
  | "duration.seconds"
type ScheduleInputs = z.infer<typeof scheduleInputsSchema>

export function CreateScheduleDialog({ workflowId }: { workflowId: string }) {
  const { createSchedule } = useSchedules(workflowId)
  const workspaceId = useWorkspaceId()
  const { workflow } = useWorkflow()
  const hasVersion = !!workflow?.version
  const [dialogOpen, setDialogOpen] = useState(false)
  const [startDatePickerOpen, setStartDatePickerOpen] = useState(false)
  const [endDatePickerOpen, setEndDatePickerOpen] = useState(false)
  const form = useForm<ScheduleInputs>({
    resolver: zodResolver(scheduleInputsSchema),
    defaultValues: {
      mode: "interval",
      duration: {
        years: 0,
        months: 0,
        weeks: 0,
        days: 0,
        hours: 0,
        minutes: 0,
        seconds: 0,
      },
      cronExpression: "",
      timeout: undefined,
      offset: "",
      startAt: "",
      endAt: "",
    },
  })
  const mode = form.watch("mode")

  useEffect(() => {
    if (mode === "cron") {
      form.setValue("offset", "")
      form.clearErrors(["offset"])
    } else {
      form.clearErrors(["cronExpression"])
    }
  }, [form, mode])

  const onSubmit = async (values: ScheduleInputs) => {
    if (!hasVersion) {
      toast({
        title: "Cannot create schedule",
        description: "You must commit the workflow before creating a schedule.",
      })
      return
    }

    const { mode, duration, cronExpression, timeout, offset, startAt, endAt } =
      values
    try {
      const payload: SchedulesCreateScheduleData["requestBody"] = {
        workflow_id: workflowId,
      }

      const sanitizedTimeout =
        typeof timeout === "number" && !Number.isNaN(timeout)
          ? timeout
          : undefined
      if (sanitizedTimeout !== undefined) {
        payload.timeout = sanitizedTimeout
      }

      if (mode === "interval") {
        const parsedDuration = durationSchema.parse(duration)
        payload.every = durationToISOString(parsedDuration)
        if (offset && offset.trim() !== "") {
          payload.offset = offset
        }
      } else {
        const cron = cronExpression?.trim()
        if (cron) {
          payload.cron = cron
        }
      }

      const convertDateTime = (value?: string) => {
        const trimmed = value?.trim()
        if (!trimmed) return undefined
        const parsed = Date.parse(trimmed)
        return Number.isNaN(parsed) ? undefined : new Date(parsed).toISOString()
      }

      const startAtIso = convertDateTime(startAt)
      if (startAtIso) {
        payload.start_at = startAtIso
      }

      const endAtIso = convertDateTime(endAt)
      if (endAtIso) {
        payload.end_at = endAtIso
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
                  <span>Create Schedule</span>
                </Button>
              </DialogTrigger>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <p>You must save the workflow before creating a schedule.</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <DialogContent className="max-h-[calc(100vh-4rem)] grid-rows-[auto,1fr] overflow-hidden p-0 sm:max-w-xl">
        <DialogHeader className="border-b px-6 py-4 text-left">
          <DialogTitle>Create a new schedule</DialogTitle>
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
            className="flex h-full flex-col overflow-hidden"
            onSubmit={form.handleSubmit(onSubmit, () => {
              console.error("Form validation failed")
              toast({
                title: "Invalid inputs in form",
                description: "Please check the form for errors.",
              })
            })}
          >
            <ScrollArea className="flex-1 px-6">
              <div className="space-y-4 py-4">
                <FormField
                  control={form.control}
                  name="mode"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs capitalize text-foreground/80">
                        Schedule Type
                      </FormLabel>
                      <FormDescription className="text-xs">
                        Choose between interval-based or cron-based scheduling.
                      </FormDescription>
                      <FormControl>
                        <Select
                          value={field.value}
                          onValueChange={field.onChange}
                        >
                          <SelectTrigger className="text-xs capitalize">
                            <SelectValue placeholder="Select schedule type" />
                          </SelectTrigger>
                          <SelectContent className="text-xs">
                            <SelectItem value="interval">Interval</SelectItem>
                            <SelectItem value="cron">Cron</SelectItem>
                          </SelectContent>
                        </Select>
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {mode === "interval" && (
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      "duration.years",
                      "duration.months",
                      "duration.days",
                      "duration.hours",
                      "duration.minutes",
                      "duration.seconds",
                    ].map((unit) => (
                      <FormField
                        key={unit}
                        control={form.control}
                        name={unit as DurationType}
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-xs capitalize text-foreground/80">
                              {unit.split(".")[1]}
                            </FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                className="text-xs capitalize"
                                placeholder={unit}
                                value={Math.max(0, Number(field.value || 0))}
                                {...form.register(unit as DurationType, {
                                  valueAsNumber: true,
                                })}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    ))}
                  </div>
                )}

                {mode === "cron" && (
                  <FormField
                    control={form.control}
                    name="cronExpression"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-xs capitalize text-foreground/80">
                          Cron Expression
                        </FormLabel>
                        <FormDescription className="text-xs">
                          Standard 5 or 6 field cron format, e.g.{" "}
                          <code className="font-mono">0 0 * * *</code>.
                        </FormDescription>
                        <FormControl>
                          <Input
                            type="text"
                            className="text-xs font-mono"
                            placeholder="0 0 * * *"
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}

                <FormField
                  key="timeout"
                  control={form.control}
                  name="timeout"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs capitalize text-foreground/80">
                        Timeout
                      </FormLabel>
                      <FormDescription className="text-xs">
                        The maximum time in seconds the workflow can run for.
                        Default is 0 (no timeout).
                      </FormDescription>
                      <FormControl>
                        <Input
                          type="number"
                          className="text-xs capitalize"
                          placeholder="Timeout (seconds)"
                          {...field}
                          onChange={(e) =>
                            field.onChange(
                              e.target.value
                                ? parseFloat(e.target.value)
                                : undefined
                            )
                          }
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {mode === "interval" && (
                  <FormField
                    key="offset"
                    control={form.control}
                    name="offset"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-xs capitalize text-foreground/80">
                          Offset
                        </FormLabel>
                        <FormDescription className="text-xs">
                          Optional delay before the first execution. Use ISO
                          8601 duration format: PT1H (1 hour), P1D (1 day),
                          PT30M (30 minutes).
                        </FormDescription>
                        <FormControl>
                          <Input
                            type="text"
                            className="text-xs"
                            placeholder="PT1H (optional)"
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}

                <FormField
                  key="startAt"
                  control={form.control}
                  name="startAt"
                  render={({ field }) => {
                    const dateValue = field.value
                      ? new Date(field.value)
                      : undefined
                    const timeValue = field.value
                      ? new Date(field.value).toTimeString().slice(0, 8)
                      : ""

                    const handleDateChange = (date: Date | undefined) => {
                      if (!date) {
                        field.onChange("")
                        setStartDatePickerOpen(false)
                        return
                      }

                      const currentTime = field.value
                        ? new Date(field.value).toTimeString().slice(0, 8)
                        : "00:00:00"
                      const [hours, minutes, seconds] = currentTime.split(":")
                      date.setHours(
                        Number.parseInt(hours),
                        Number.parseInt(minutes),
                        Number.parseInt(seconds)
                      )
                      field.onChange(date.toISOString())
                      setStartDatePickerOpen(false)
                    }

                    const handleTimeChange = (
                      e: React.ChangeEvent<HTMLInputElement>
                    ) => {
                      const timeStr = e.target.value
                      const currentDate = field.value
                        ? new Date(field.value)
                        : new Date()

                      const [hours, minutes, seconds] = timeStr.split(":")
                      currentDate.setHours(
                        Number.parseInt(hours || "0"),
                        Number.parseInt(minutes || "0"),
                        Number.parseInt(seconds || "0")
                      )
                      field.onChange(currentDate.toISOString())
                    }

                    return (
                      <FormItem>
                        <FormLabel className="text-xs capitalize text-foreground/80">
                          Start after
                        </FormLabel>
                        <FormDescription className="text-xs">
                          Optional earliest date and time to begin running this
                          schedule.
                        </FormDescription>
                        <FormControl>
                          <div className="flex gap-4">
                            <div className="flex flex-col gap-3">
                              <Popover
                                open={startDatePickerOpen}
                                onOpenChange={setStartDatePickerOpen}
                              >
                                <PopoverTrigger asChild>
                                  <Button
                                    variant="outline"
                                    className="w-32 justify-between font-normal text-xs"
                                  >
                                    {dateValue
                                      ? dateValue.toLocaleDateString()
                                      : "Select date"}
                                    <ChevronDownIcon className="size-4" />
                                  </Button>
                                </PopoverTrigger>
                                <PopoverContent
                                  className="w-auto overflow-hidden p-0"
                                  align="start"
                                >
                                  <Calendar
                                    mode="single"
                                    selected={dateValue}
                                    captionLayout="dropdown"
                                    onSelect={handleDateChange}
                                  />
                                </PopoverContent>
                              </Popover>
                            </div>
                            <div className="flex flex-col gap-3">
                              <Input
                                type="time"
                                step="1"
                                value={timeValue}
                                onChange={handleTimeChange}
                                className="bg-background text-xs appearance-none [&::-webkit-calendar-picker-indicator]:hidden [&::-webkit-calendar-picker-indicator]:appearance-none"
                              />
                            </div>
                          </div>
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )
                  }}
                />

                <FormField
                  key="endAt"
                  control={form.control}
                  name="endAt"
                  render={({ field }) => {
                    const dateValue = field.value
                      ? new Date(field.value)
                      : undefined
                    const timeValue = field.value
                      ? new Date(field.value).toTimeString().slice(0, 8)
                      : ""

                    const handleDateChange = (date: Date | undefined) => {
                      if (!date) {
                        field.onChange("")
                        setEndDatePickerOpen(false)
                        return
                      }

                      const currentTime = field.value
                        ? new Date(field.value).toTimeString().slice(0, 8)
                        : "00:00:00"
                      const [hours, minutes, seconds] = currentTime.split(":")
                      date.setHours(
                        Number.parseInt(hours),
                        Number.parseInt(minutes),
                        Number.parseInt(seconds)
                      )
                      field.onChange(date.toISOString())
                      setEndDatePickerOpen(false)
                    }

                    const handleTimeChange = (
                      e: React.ChangeEvent<HTMLInputElement>
                    ) => {
                      const timeStr = e.target.value
                      const currentDate = field.value
                        ? new Date(field.value)
                        : new Date()

                      const [hours, minutes, seconds] = timeStr.split(":")
                      currentDate.setHours(
                        Number.parseInt(hours || "0"),
                        Number.parseInt(minutes || "0"),
                        Number.parseInt(seconds || "0")
                      )
                      field.onChange(currentDate.toISOString())
                    }

                    return (
                      <FormItem>
                        <FormLabel className="text-xs capitalize text-foreground/80">
                          End by
                        </FormLabel>
                        <FormDescription className="text-xs">
                          Optional latest date and time after which the schedule
                          will stop running.
                        </FormDescription>
                        <FormControl>
                          <div className="flex gap-4">
                            <div className="flex flex-col gap-3">
                              <Popover
                                open={endDatePickerOpen}
                                onOpenChange={setEndDatePickerOpen}
                              >
                                <PopoverTrigger asChild>
                                  <Button
                                    variant="outline"
                                    className="w-32 justify-between font-normal text-xs"
                                  >
                                    {dateValue
                                      ? dateValue.toLocaleDateString()
                                      : "Select date"}
                                    <ChevronDownIcon className="size-4" />
                                  </Button>
                                </PopoverTrigger>
                                <PopoverContent
                                  className="w-auto overflow-hidden p-0"
                                  align="start"
                                >
                                  <Calendar
                                    mode="single"
                                    selected={dateValue}
                                    captionLayout="dropdown"
                                    onSelect={handleDateChange}
                                  />
                                </PopoverContent>
                              </Popover>
                            </div>
                            <div className="flex flex-col gap-3">
                              <Input
                                type="time"
                                step="1"
                                value={timeValue}
                                onChange={handleTimeChange}
                                className="bg-background text-xs appearance-none [&::-webkit-calendar-picker-indicator]:hidden [&::-webkit-calendar-picker-indicator]:appearance-none"
                              />
                            </div>
                          </div>
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )
                  }}
                />
              </div>
            </ScrollArea>
            <DialogFooter className="border-t px-6 py-4">
              <Button type="submit" variant="default">
                <PlusCircleIcon className="mr-2 size-4" />
                <span>Create</span>
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
