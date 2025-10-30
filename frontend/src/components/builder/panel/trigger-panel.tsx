"use client"

import "react18-json-view/src/style.css"

import { zodResolver } from "@hookform/resolvers/zod"
import { CheckIcon, DotsHorizontalIcon } from "@radix-ui/react-icons"
import * as ipaddr from "ipaddr.js"
import {
  CalendarClockIcon,
  KeyIcon,
  PlusCircleIcon,
  SaveIcon,
  WebhookIcon,
} from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  $WebhookMethod,
  $WebhookStatus,
  ApiError,
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
import { CustomTagInput, type Tag } from "@/components/tags-input"
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
import {
  Dialog,
  DialogClose,
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
import { Badge } from "@/components/ui/badge"
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  useGenerateWebhookApiKey,
  useSchedules,
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
    const normalized = `${toCanonicalString(address)}/${prefixLength}`
    return { normalized }
  } catch {
    try {
      const address = ipaddr.parse(value)
      const prefixLength = address.kind() === "ipv4" ? 32 : 128
      const normalized = `${toCanonicalString(address)}/${prefixLength}`
      return { normalized }
    } catch {
      return {
        error: `Invalid IP address or CIDR: "${value}"`,
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
          "trigger-schedules",
        ]}
      >
        {/* Webhooks */}
        <AccordionItem value="trigger-webhooks">
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
        <AccordionItem value="trigger-schedules">
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
  const apiKeySuffix = webhook.api_key?.suffix ?? null
  const apiKeyCreatedAt = webhook.api_key?.created_at ?? null
  const apiKeyLastUsedAt = webhook.api_key?.last_used_at ?? null

  const workspaceId = useWorkspaceId()
  const { mutateAsync, isPending: isUpdatingWebhook } = useUpdateWebhook(
    workspaceId,
    workflowId
  )
  const { mutateAsync: generateWebhookApiKey, isPending: isGeneratingApiKey } =
    useGenerateWebhookApiKey(workspaceId, workflowId)
  const [generatedApiKey, setGeneratedApiKey] = useState<string | null>(null)
  const [generatedAt, setGeneratedAt] = useState<string | null>(null)
  const [apiKeyDialogOpen, setApiKeyDialogOpen] = useState(false)

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

  const allowlistFieldState = form.getFieldState(
    "allowlisted_cidrs",
    form.formState
  )
  const allowlistDirty = allowlistFieldState.isDirty

  const handleAllowlistedCidrsChange = useCallback(
    (newTags: Tag[] | undefined) => {
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
      } else {
        form.clearErrors("allowlisted_cidrs")
      }

      form.setValue("allowlisted_cidrs", Array.from(normalized.values()), {
        shouldDirty: true,
        shouldValidate: !firstError,
        shouldTouch: true,
      })
    },
    [form]
  )

  const handleSaveAllowlistedCidrs = useCallback(async () => {
    const isValid = await form.trigger("allowlisted_cidrs")
    if (!isValid) {
      toast({
        title: "Cannot save allowlist",
        description: "Please fix the highlighted errors before saving.",
        variant: "destructive",
      })
      return
    }

    const tags = form.getValues("allowlisted_cidrs")
    const payload = tags.map((tag) => tag.text)

    try {
      await mutateAsync({ allowlisted_cidrs: payload })
      const currentValues = form.getValues()
      form.reset(currentValues)
      form.clearErrors("allowlisted_cidrs")
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
  }, [form, mutateAsync])

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

  const handleGenerateApiKey = async () => {
    try {
      const response = await generateWebhookApiKey()
      setGeneratedApiKey(response.api_key)
      setGeneratedAt(response.created_at ?? null)
      setApiKeyDialogOpen(true)
      toast({
        title: "API key generated",
        description: "Copy the API key now. It will not be shown again.",
      })
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
    }
  }

  const handleDialogChange = (open: boolean) => {
    setApiKeyDialogOpen(open)
    if (!open) {
      setGeneratedApiKey(null)
      setGeneratedAt(null)
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
                      role="combobox"
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
              <FormMessage className="text-[11px]" />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="allowlisted_cidrs"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-end justify-between gap-2 min-h-[20px]">
                <FormLabel className="text-xs font-medium">
                  <span>IP Allowlist</span>
                </FormLabel>
                {allowlistDirty && (
                  <Button
                    type="button"
                    variant="secondary"
                    className="h-auto px-2 text-[11px] py-0 flex items-center gap-1"
                    onClick={handleSaveAllowlistedCidrs}
                    disabled={isUpdatingWebhook}
                  >
                    {isUpdatingWebhook ? (
                      <>
                        <SaveIcon className="size-3 animate-pulse" />
                        <span>Saving...</span>
                      </>
                    ) : (
                      <>
                        <SaveIcon className="size-3" />
                        <span>Save</span>
                      </>
                    )}
                  </Button>
                )}
              </div>
              <FormControl>
                <CustomTagInput
                  {...field}
                  placeholder="Enter an IP address or CIDR..."
                  tags={field.value}
                  setTags={(newTags) =>
                    handleAllowlistedCidrsChange(
                      Array.isArray(newTags) ? newTags : []
                    )
                  }
                />
              </FormControl>
              {field.value.length === 0 && (
                <FormDescription className="text-xs">
                  No allowlist entries are configured. All source IPs are
                  allowed.
                </FormDescription>
              )}
              <FormMessage className="text-[11px]" />
              <FormDescription className="text-[11px]">
                Enter a valid IPv4 or IPv6 address or CIDR (e.g., 203.0.113.7,
                203.0.113.0/24, or 2001:db8::/32).
              </FormDescription>
            </FormItem>
          )}
        />
      </Form>

      <div className="space-y-3">
        <Label className="flex items-center gap-2 text-xs font-medium">
          <KeyIcon className="size-4 text-muted-foreground" />
          <span>API Key</span>
        </Label>
        {hasActiveApiKey ? (
          <div className="rounded-lg border bg-muted/40 p-4 text-xs shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Active key
              </span>
              <Badge
                variant="outline"
                className="font-mono text-[11px] uppercase tracking-widest"
              >
                {apiKeySuffix ? `•••${apiKeySuffix}` : "—"}
              </Badge>
            </div>
            <Separator className="my-3" />
            <dl className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  Created
                </dt>
                <dd className="font-medium text-foreground">
                  {formatTimestamp(apiKeyCreatedAt)}
                </dd>
              </div>
              <div className="space-y-1">
                <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  Last used
                </dt>
                <dd className="font-medium text-foreground">
                  {formatTimestamp(apiKeyLastUsedAt)}
                </dd>
              </div>
            </dl>
            <Button
              variant="secondary"
              size="sm"
              className="mt-4 w-full justify-center"
              onClick={handleGenerateApiKey}
              disabled={isGeneratingApiKey}
            >
              {isGeneratingApiKey ? "Generating..." : "Regenerate API key"}
            </Button>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed bg-muted/30 p-4 text-xs shadow-sm">
            <div className="space-y-2 text-foreground">
              <p className="text-sm font-medium">No API key configured</p>
              <p className="text-[11px] text-muted-foreground">
                Generate an API key to require clients to authenticate webhook
                requests.
              </p>
            </div>
            <Button
              size="sm"
              className="mt-4 w-full justify-center"
              onClick={handleGenerateApiKey}
              disabled={isGeneratingApiKey}
            >
              {isGeneratingApiKey ? "Generating..." : "Generate API key"}
            </Button>
          </div>
        )}
        <p className="text-[11px] text-muted-foreground">
          API keys are shown only once after creation. Store them securely.
        </p>
      </div>

      <Dialog open={apiKeyDialogOpen} onOpenChange={handleDialogChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Webhook API key generated</DialogTitle>
            <DialogDescription>
              Copy the key now. You will not be able to view it again once this
              dialog is closed.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-xs">
            <div className="flex justify-between text-muted-foreground">
              <span>Generated</span>
              <span>{formatTimestamp(generatedAt)}</span>
            </div>
            <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
              <code className="max-w-[75%] break-all text-xs">
                {generatedApiKey ?? "—"}
              </code>
              {generatedApiKey ? (
                <CopyButton
                  value={generatedApiKey}
                  toastMessage="Copied API key to clipboard"
                />
              ) : null}
            </div>
            <p className="text-[11px] text-muted-foreground">
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
            <TableHead className="pl-3 text-xs font-semibold">
              Schedule ID
            </TableHead>
            <TableHead className="text-xs font-semibold">Type</TableHead>
            <TableHead className="text-xs font-semibold">Schedule</TableHead>
            <TableHead className="text-xs font-semibold">Status</TableHead>
            <TableHead className="text-xs font-semibold">Timeout</TableHead>
            <TableHead className="text-xs font-semibold">Offset</TableHead>
            <TableHead className="text-right text-xs font-semibold">
              Actions
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {schedules.length > 0 ? (
            schedules.map(({ id, status, every, cron, timeout, offset }) => {
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

              return (
                <TableRow key={id} className="ext-xs text-muted-foreground">
                  <TableCell className="items-center pl-3 text-xs">
                    {id}
                  </TableCell>
                  <TableCell className="items-center text-xs">
                    {isCron ? "Cron" : "Interval"}
                  </TableCell>
                  <TableCell className="items-center text-xs">
                    {isCron ? (
                      <code className="rounded bg-muted px-1 py-0.5 text-[11px]">
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
                              onClick={() => navigator.clipboard.writeText(id!)}
                              className="text-xs"
                            >
                              Copy ID
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              className={cn("text-xs", status === "online")}
                              onClick={async () =>
                                await updateSchedule({
                                  workspaceId,
                                  scheduleId: id!,
                                  requestBody: {
                                    status:
                                      status === "online"
                                        ? "offline"
                                        : "online",
                                  },
                                })
                              }
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
                            <AlertDialogTitle>Delete schedule</AlertDialogTitle>
                            <AlertDialogDescription>
                              Are you sure you want to delete this schedule?
                              This action cannot be undone.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              variant="destructive"
                              onClick={async () =>
                                await deleteSchedule({
                                  workspaceId,
                                  scheduleId: id!,
                                })
                              }
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
            })
          ) : (
            <TableRow className="justify-center text-xs text-muted-foreground">
              <TableCell
                className="h-8 text-center bg-muted-foreground/5"
                colSpan={7}
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
        variant: "destructive",
      })
      return
    }

    const { mode, duration, cronExpression, timeout, offset } = values
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

      const response = await createSchedule({
        workspaceId,
        requestBody: payload,
      })
      console.log("Schedule created", response)
      form.reset()
    } catch (error) {
      if (error instanceof ApiError) {
        console.error("Failed to create schedule", error.body)
      } else {
        console.error("Unexpected error when creating schedule", error)
      }
    }
  }

  return (
    <Dialog>
      <TooltipProvider>
        <Tooltip open={!hasVersion ? undefined : false}>
          <TooltipTrigger asChild>
            <span tabIndex={0}>
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
      <DialogContent>
        <DialogHeader>
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
            className="space-y-4"
            onSubmit={form.handleSubmit(onSubmit, () => {
              console.error("Form validation failed")
              toast({
                title: "Invalid inputs in form",
                description: "Please check the form for errors.",
              })
            })}
          >
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
                    <Select value={field.value} onValueChange={field.onChange}>
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
                      Optional delay before the first execution. Use ISO 8601
                      duration format: PT1H (1 hour), P1D (1 day), PT30M (30
                      minutes).
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
            <DialogFooter className="mt-4">
              <DialogClose asChild>
                <Button type="submit" variant="default">
                  <PlusCircleIcon className="mr-2 size-4" />
                  <span>Create</span>
                </Button>
              </DialogClose>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
