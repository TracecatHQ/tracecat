"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { LucideIcon } from "lucide-react"
import {
  AlignLeft,
  ArrowUpRight,
  BriefcaseIcon,
  CheckSquare,
  CircleCheck,
  Eye,
  FilePlus2,
  Flag,
  Flame,
  GitCompare,
  MessageSquareOff,
  MessageSquarePlus,
  MessageSquareText,
  Paperclip,
  PenSquare,
  PlusCircleIcon,
  RotateCcw,
  SquareStack,
  Tag,
  Trash2Icon,
  UserRound,
  Zap,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { v4 as uuidv4 } from "uuid"
import { z } from "zod"
import type { CaseEventType, WorkflowRead } from "@/client"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { Button } from "@/components/ui/button"
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
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
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
import { toast } from "@/components/ui/use-toast"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkflowBuilder } from "@/providers/builder"

// Event type option with icon
interface CaseTriggerEventOption {
  value: CaseEventType
  label: string
  icon: LucideIcon
}

// Event types that can trigger workflows with friendly labels and icons
const CASE_TRIGGER_EVENT_OPTIONS: CaseTriggerEventOption[] = [
  { value: "case_created", label: "Case created", icon: FilePlus2 },
  { value: "case_updated", label: "Case updated", icon: PenSquare },
  { value: "case_closed", label: "Case closed", icon: CircleCheck },
  { value: "case_reopened", label: "Case reopened", icon: RotateCcw },
  { value: "case_viewed", label: "Case viewed", icon: Eye },
  { value: "priority_changed", label: "Priority changed", icon: Flag },
  { value: "severity_changed", label: "Severity changed", icon: Flame },
  { value: "status_changed", label: "Status changed", icon: GitCompare },
  { value: "fields_changed", label: "Fields changed", icon: PenSquare },
  { value: "assignee_changed", label: "Assignee changed", icon: UserRound },
  { value: "attachment_created", label: "Attachment created", icon: Paperclip },
  { value: "attachment_deleted", label: "Attachment deleted", icon: Paperclip },
  { value: "tag_added", label: "Tag added", icon: Tag },
  { value: "tag_removed", label: "Tag removed", icon: Tag },
  { value: "payload_changed", label: "Payload changed", icon: SquareStack },
  { value: "task_created", label: "Task created", icon: CheckSquare },
  { value: "task_deleted", label: "Task deleted", icon: CheckSquare },
  {
    value: "task_status_changed",
    label: "Task status changed",
    icon: CheckSquare,
  },
  {
    value: "task_priority_changed",
    label: "Task priority changed",
    icon: CheckSquare,
  },
  {
    value: "task_workflow_changed",
    label: "Task workflow changed",
    icon: CheckSquare,
  },
  {
    value: "task_assignee_changed",
    label: "Task assignee changed",
    icon: CheckSquare,
  },
]

// Field subtypes for case_updated event type
type UpdatedFieldSubtype =
  | "summary"
  | "description"
  | "comment_added"
  | "comment_removed"
  | "comment_updated"

// Update type option with icon
interface UpdateTypeOption {
  value: UpdatedFieldSubtype
  label: string
  icon: LucideIcon
}

type CaseTriggerExecutionMode = "published_only" | "draft_only" | "always"

interface ExecutionModeOption {
  value: CaseTriggerExecutionMode
  label: string
  description: string
}

const EXECUTION_MODE_OPTIONS: ExecutionModeOption[] = [
  {
    value: "published_only",
    label: "Published only",
    description: "Use the latest committed definition.",
  },
  {
    value: "draft_only",
    label: "Draft only",
    description: "Use the current draft workflow graph.",
  },
  {
    value: "always",
    label: "Always (draft + published)",
    description: "Dispatch both draft and published executions when available.",
  },
]

// Update type options with friendly labels and icons
const UPDATE_TYPE_OPTIONS: UpdateTypeOption[] = [
  { value: "summary", label: "Summary changed", icon: PenSquare },
  { value: "description", label: "Description changed", icon: AlignLeft },
  { value: "comment_added", label: "Comment added", icon: MessageSquarePlus },
  {
    value: "comment_removed",
    label: "Comment removed",
    icon: MessageSquareOff,
  },
  {
    value: "comment_updated",
    label: "Comment updated",
    icon: MessageSquareText,
  },
]

// Helper to get update type option by value
function getUpdateTypeOption(value: UpdatedFieldSubtype): UpdateTypeOption {
  return (
    UPDATE_TYPE_OPTIONS.find((opt) => opt.value === value) ?? {
      value,
      label: value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      icon: PenSquare,
    }
  )
}

function getExecutionModeOption(
  value: CaseTriggerExecutionMode
): ExecutionModeOption {
  return (
    EXECUTION_MODE_OPTIONS.find((opt) => opt.value === value) ?? {
      value,
      label: value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      description: "",
    }
  )
}

function normalizeExecutionMode(
  value: string | undefined
): CaseTriggerExecutionMode {
  if (value === "draft") {
    return "draft_only"
  }
  if (value === "published") {
    return "published_only"
  }
  if (EXECUTION_MODE_OPTIONS.some((opt) => opt.value === value)) {
    return value as CaseTriggerExecutionMode
  }
  return "published_only"
}

// Case trigger config stored in workflow.object.nodes[].data.caseTriggers
interface CaseTriggerConfig {
  id: string
  enabled: boolean
  eventType: CaseEventType
  fieldFilters: Record<string, unknown>
  allowSelfTrigger: boolean
  executionMode: CaseTriggerExecutionMode
}

type WorkflowObject = {
  nodes?: Array<{
    type?: string
    data?: { caseTriggers?: CaseTriggerConfig[] }
  }>
}

type WorkflowReadWithObject = WorkflowRead & { object?: WorkflowObject }

interface CaseTriggersControlsProps {
  workflow: WorkflowReadWithObject
}

// Helper to get event option by value
function getEventOption(value: CaseEventType): CaseTriggerEventOption {
  return (
    CASE_TRIGGER_EVENT_OPTIONS.find((opt) => opt.value === value) ?? {
      value,
      label: value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      icon: Zap,
    }
  )
}

// Helper to check if a trigger is a duplicate (same eventType and fieldFilters)
function isTriggerDuplicate(
  newConfig: Omit<CaseTriggerConfig, "id">,
  existingTriggers: CaseTriggerConfig[]
): boolean {
  return existingTriggers.some(
    (existing) =>
      existing.eventType === newConfig.eventType &&
      existing.executionMode === newConfig.executionMode &&
      JSON.stringify(existing.fieldFilters) ===
        JSON.stringify(newConfig.fieldFilters)
  )
}

// Form schema for case trigger dialog
const caseTriggerFormSchema = z.object({
  eventType: z.string().min(1, "Event type is required"),
  fieldSubtype: z.string().optional(),
  allowSelfTrigger: z.boolean().default(false),
  executionMode: z
    .enum(["published_only", "draft_only", "always"])
    .default("published_only"),
})

type CaseTriggerFormValues = z.infer<typeof caseTriggerFormSchema>

// Placeholder value for "Any" option since Radix Select doesn't allow empty strings
const ANY_SUBTYPE_VALUE = "__any__"

interface AddCaseTriggerDialogProps {
  onAdd: (config: Omit<CaseTriggerConfig, "id">) => boolean // Returns success
  isUpdating: boolean
}

function AddCaseTriggerDialog({
  onAdd,
  isUpdating,
}: AddCaseTriggerDialogProps) {
  const [open, setOpen] = useState(false)

  const form = useForm<CaseTriggerFormValues>({
    resolver: zodResolver(caseTriggerFormSchema),
    defaultValues: {
      eventType: "case_updated",
      fieldSubtype: "",
      allowSelfTrigger: false,
      executionMode: "published_only",
    },
  })

  const eventType = form.watch("eventType") as CaseEventType
  const executionMode = form.watch("executionMode") as CaseTriggerExecutionMode

  useEffect(() => {
    if (open) {
      form.reset({
        eventType: "case_updated",
        fieldSubtype: "",
        allowSelfTrigger: false,
        executionMode: "published_only",
      })
    }
  }, [open, form])

  // Clear field subtype when event type changes away from case_updated
  useEffect(() => {
    if (eventType !== "case_updated") {
      form.setValue("fieldSubtype", "")
    }
  }, [eventType, form])

  const onSubmit = (values: CaseTriggerFormValues) => {
    const fieldFilters: Record<string, unknown> = {}
    if (
      values.eventType === "case_updated" &&
      values.fieldSubtype &&
      values.fieldSubtype !== ANY_SUBTYPE_VALUE
    ) {
      fieldFilters["data.field"] = values.fieldSubtype
    }

    const success = onAdd({
      enabled: true,
      eventType: values.eventType as CaseEventType,
      fieldFilters,
      allowSelfTrigger: values.allowSelfTrigger,
      executionMode: values.executionMode as CaseTriggerExecutionMode,
    })

    if (success) {
      setOpen(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="flex gap-2 justify-center items-center w-full h-7 text-muted-foreground"
          disabled={isUpdating}
        >
          <PlusCircleIcon className="size-4" />
          <span>Add case trigger</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>Add case trigger</DialogTitle>
          <DialogDescription>
            Case triggers allow this workflow to be automatically triggered when
            case events occur. Enable "Self-trigger" to allow events caused by
            this workflow to re-trigger it.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="eventType"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-xs">Event type</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select event type" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {CASE_TRIGGER_EVENT_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          <span className="flex items-center gap-2">
                            <option.icon className="size-3.5 text-muted-foreground" />
                            <span>{option.label}</span>
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            {eventType === "case_updated" && (
              <FormField
                control={form.control}
                name="fieldSubtype"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Update type</FormLabel>
                    <Select
                      value={field.value || ANY_SUBTYPE_VALUE}
                      onValueChange={(val) =>
                        field.onChange(val === ANY_SUBTYPE_VALUE ? "" : val)
                      }
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Any update" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value={ANY_SUBTYPE_VALUE}>
                          <span className="flex items-center gap-2 text-muted-foreground">
                            <Zap className="size-3.5" />
                            <span>Any update</span>
                          </span>
                        </SelectItem>
                        {UPDATE_TYPE_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            <span className="flex items-center gap-2">
                              <option.icon className="size-3.5 text-muted-foreground" />
                              <span>{option.label}</span>
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <FormField
              control={form.control}
              name="executionMode"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-xs">Execution mode</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select mode" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {EXECUTION_MODE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          <span className="flex items-center gap-2">
                            <span>{option.label}</span>
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription className="text-xs">
                    {getExecutionModeOption(executionMode).description}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="allowSelfTrigger"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel className="text-xs">Self-trigger</FormLabel>
                    <FormDescription className="text-xs">
                      Allow events caused by this workflow to re-trigger it.
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isUpdating}>
                Add trigger
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export function CaseTriggersControls({ workflow }: CaseTriggersControlsProps) {
  const { isFeatureEnabled, isLoading: featureFlagLoading } = useFeatureFlag()
  const { setNodes, reactFlow } = useWorkflowBuilder()
  const isUpdating = false

  // Parse existing triggers from workflow.object
  const existingTriggers = useMemo(() => {
    if (!workflow.object) return []
    const nodes = workflow.object.nodes ?? []
    const triggerNode = nodes.find((n) => n.type === "trigger")
    return (triggerNode?.data?.caseTriggers ?? []).map((trigger) => ({
      ...trigger,
      executionMode: normalizeExecutionMode(trigger.executionMode),
    }))
  }, [workflow.object])

  const [triggers, setTriggers] =
    useState<CaseTriggerConfig[]>(existingTriggers)

  // Sync local state when workflow changes
  useEffect(() => {
    setTriggers(existingTriggers)
  }, [existingTriggers])

  const saveTriggers = useCallback(
    (updateFn: (current: CaseTriggerConfig[]) => CaseTriggerConfig[]) => {
      // Get the latest nodes from ReactFlow instance to avoid race conditions
      // with local state or stale props
      const nodes = reactFlow.getNodes()
      const triggerNode = nodes.find((n) => n.type === "trigger")
      const currentTriggers =
        (triggerNode?.data?.caseTriggers as CaseTriggerConfig[]) ?? []

      const normalizedTriggers = currentTriggers.map((trigger) => ({
        ...trigger,
        executionMode: normalizeExecutionMode(trigger.executionMode),
      }))

      const newTriggers = updateFn(normalizedTriggers)

      setTriggers(newTriggers)
      setNodes((nodes) =>
        nodes.map((n) => {
          if (n.type === "trigger") {
            return {
              ...n,
              data: {
                ...n.data,
                caseTriggers: newTriggers,
              },
            }
          }
          return n
        })
      )
      toast({
        title: "Case triggers saved",
        description: "Case trigger configuration updated successfully.",
      })
    },
    [setNodes, reactFlow]
  )

  const handleAddTrigger = useCallback(
    (config: Omit<CaseTriggerConfig, "id">): boolean => {
      // Get current triggers from ReactFlow to check for duplicates
      const nodes = reactFlow.getNodes()
      const triggerNode = nodes.find((n) => n.type === "trigger")
      const currentTriggers =
        (triggerNode?.data?.caseTriggers as CaseTriggerConfig[]) ?? []
      const normalizedTriggers = currentTriggers.map((trigger) => ({
        ...trigger,
        executionMode: normalizeExecutionMode(trigger.executionMode),
      }))

      // Check for duplicate trigger
      if (isTriggerDuplicate(config, normalizedTriggers)) {
        toast({
          title: "Duplicate trigger",
          description:
            "A trigger with the same event type and filters already exists.",
          variant: "destructive",
        })
        return false
      }

      saveTriggers((current) => {
        const newTrigger: CaseTriggerConfig = {
          id: uuidv4(),
          ...config,
        }
        return [...current, newTrigger]
      })
      return true
    },
    [saveTriggers, reactFlow]
  )

  const handleRemoveTrigger = useCallback(
    (triggerId: string) => {
      saveTriggers((current) => current.filter((t) => t.id !== triggerId))
    },
    [saveTriggers]
  )

  const handleToggleEnabled = useCallback(
    (triggerId: string, enabled: boolean) => {
      saveTriggers((current) =>
        current.map((t) => (t.id === triggerId ? { ...t, enabled } : t))
      )
    },
    [saveTriggers]
  )

  const handleAllowSelfTriggerChange = useCallback(
    (triggerId: string, allowSelfTrigger: boolean) => {
      saveTriggers((current) =>
        current.map((t) =>
          t.id === triggerId ? { ...t, allowSelfTrigger } : t
        )
      )
    },
    [saveTriggers]
  )

  const handleExecutionModeChange = useCallback(
    (triggerId: string, executionMode: CaseTriggerExecutionMode) => {
      saveTriggers((current) =>
        current.map((t) => (t.id === triggerId ? { ...t, executionMode } : t))
      )
    },
    [saveTriggers]
  )

  // Show loading state while checking feature flag
  if (featureFlagLoading) {
    return null
  }

  // Show enterprise-only message if feature is not enabled
  if (!isFeatureEnabled("case-triggers")) {
    return (
      <FeatureFlagEmptyState
        title="Enterprise only"
        description="Case triggers are only available on enterprise plans."
        icon={<BriefcaseIcon className="size-6" />}
        className="py-4"
      >
        <Button
          variant="link"
          asChild
          className="text-muted-foreground"
          size="sm"
        >
          <a
            href="https://tracecat.com"
            target="_blank"
            rel="noopener noreferrer"
          >
            Learn more <ArrowUpRight className="size-4" />
          </a>
        </Button>
      </FeatureFlagEmptyState>
    )
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-3 text-xs font-semibold">
              Enabled
            </TableHead>
            <TableHead className="text-xs font-semibold">Event type</TableHead>
            <TableHead className="text-xs font-semibold">Update type</TableHead>
            <TableHead className="text-xs font-semibold">Execution</TableHead>
            <TableHead className="text-xs font-semibold">
              Self-trigger
            </TableHead>
            <TableHead className="text-right text-xs font-semibold pr-3">
              Actions
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {triggers.length > 0 ? (
            triggers.map((trigger) => {
              const eventOption = getEventOption(trigger.eventType)
              const EventIcon = eventOption.icon
              const fieldSubtype = trigger.fieldFilters["data.field"] as
                | UpdatedFieldSubtype
                | undefined

              return (
                <TableRow
                  key={trigger.id}
                  className="text-xs text-muted-foreground"
                >
                  <TableCell className="pl-3">
                    <Switch
                      checked={trigger.enabled}
                      onCheckedChange={(checked) =>
                        handleToggleEnabled(trigger.id, checked)
                      }
                      disabled={isUpdating}
                      className="data-[state=checked]:bg-emerald-500"
                    />
                  </TableCell>
                  <TableCell>
                    <span className="flex items-center gap-2">
                      <EventIcon className="size-3.5 text-muted-foreground" />
                      <span>{eventOption.label}</span>
                    </span>
                  </TableCell>
                  <TableCell>
                    {trigger.eventType === "case_updated" ? (
                      fieldSubtype ? (
                        <span className="flex items-center gap-2 text-xs">
                          {(() => {
                            const opt = getUpdateTypeOption(fieldSubtype)
                            const UpdateIcon = opt.icon
                            return (
                              <>
                                <UpdateIcon className="size-3.5 text-muted-foreground" />
                                <span>{opt.label}</span>
                              </>
                            )
                          })()}
                        </span>
                      ) : (
                        <span className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Zap className="size-3.5" />
                          <span>Any update</span>
                        </span>
                      )
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Select
                      value={trigger.executionMode}
                      onValueChange={(value) =>
                        handleExecutionModeChange(
                          trigger.id,
                          value as CaseTriggerExecutionMode
                        )
                      }
                      disabled={isUpdating}
                    >
                      <SelectTrigger className="h-7 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {EXECUTION_MODE_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            <span className="flex items-center gap-2">
                              <span>{option.label}</span>
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <Switch
                      checked={trigger.allowSelfTrigger}
                      onCheckedChange={(checked) =>
                        handleAllowSelfTriggerChange(trigger.id, checked)
                      }
                      disabled={isUpdating}
                    />
                  </TableCell>
                  <TableCell className="text-right pr-3">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      onClick={() => handleRemoveTrigger(trigger.id)}
                      disabled={isUpdating}
                    >
                      <Trash2Icon className="size-4 text-destructive" />
                    </Button>
                  </TableCell>
                </TableRow>
              )
            })
          ) : (
            <TableRow className="text-xs text-muted-foreground">
              <TableCell
                className="h-8 text-center bg-muted-foreground/5"
                colSpan={6}
              >
                No case triggers configured
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      <Separator />
      <AddCaseTriggerDialog onAdd={handleAddTrigger} isUpdating={isUpdating} />
    </div>
  )
}
