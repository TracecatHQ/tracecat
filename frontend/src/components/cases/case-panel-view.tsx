"use client"

import { format, intervalToDuration, isValid as isValidDate } from "date-fns"
import {
  Activity,
  Braces,
  FlagTriangleRight,
  Hourglass,
  MessageSquare,
  MoreHorizontal,
  Paperclip,
  X,
} from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  CaseDropdownDefinitionRead,
  CaseDurationDefinitionRead,
  CaseDurationRead,
  CaseFieldRead,
  CasePriority,
  CaseSeverity,
  CaseUpdate,
  SqlType,
} from "@/client"
import { CaseAttachmentsSection } from "@/components/cases/case-attachments-section"
import { CommentSection } from "@/components/cases/case-comments-section"
import { CaseWorkflowTriggerButton } from "@/components/cases/case-panel-common"
import { CustomField } from "@/components/cases/case-panel-custom-fields"
import { CasePanelDescription } from "@/components/cases/case-panel-description"
import {
  type AssigneeInfo,
  AssigneeSelect,
  CaseDropdownSelect,
  PrioritySelect,
  SeveritySelect,
} from "@/components/cases/case-panel-selectors"
import { CasePanelSummary } from "@/components/cases/case-panel-summary"
import { CasePayloadSection } from "@/components/cases/case-payload-section"
import { CaseTasksSection } from "@/components/cases/case-tasks-section"
import { CaseWorkflowTrigger } from "@/components/cases/case-workflow-trigger"
import { CaseFeed } from "@/components/cases/cases-feed"
import { AlertNotification } from "@/components/notifications"
import { TagBadge } from "@/components/tag-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import {
  useAddCaseTag,
  useCaseDropdownDefinitions,
  useCaseDurationDefinitions,
  useCaseDurations,
  useCaseTagCatalog,
  useGetCase,
  useRemoveCaseTag,
  useSetCaseDropdownValue,
  useUpdateCase,
} from "@/lib/hooks"
import { parseISODuration } from "@/lib/time"
import { undoSlugify } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type CasePanelTab = "comments" | "activity" | "attachments" | "payload"

function isCustomFieldValueEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === "string") return value.trim().length === 0
  if (typeof value === "number") return Number.isNaN(value)
  if (typeof value === "boolean") return false
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === "object")
    return Object.keys(value as object).length === 0
  return false
}

function getFormattedDateValue(value: unknown): string | null {
  if (value instanceof Date) {
    return isValidDate(value) ? format(value, "MMM d yyyy '·' p") : null
  }
  if (typeof value === "string" && value.length > 0) {
    const parsed = new Date(value)
    if (isValidDate(parsed)) {
      return format(parsed, "MMM d yyyy '·' p")
    }
  }
  return null
}

function getCustomFieldInputWidth(value: unknown, type?: SqlType): string {
  const baseLength = (() => {
    const formattedDate = getFormattedDateValue(value)
    if (formattedDate) return formattedDate.length
    if (
      (type === "TIMESTAMP" || type === "TIMESTAMPTZ") &&
      (value === null ||
        value === undefined ||
        (typeof value === "string" && value.trim().length === 0))
    ) {
      return "Select date and time".length
    }
    if (value === null || value === undefined) return 5
    if (typeof value === "string")
      return Math.max(value.trim().length, value.length)
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value).length
    }
    if (Array.isArray(value)) {
      return Math.min(JSON.stringify(value).length, 24)
    }
    if (typeof value === "object") {
      return Math.min(JSON.stringify(value).length, 24)
    }
    return 5
  })()

  const min = 8
  const max = 28
  const widthInCh = Math.min(Math.max(baseLength + 4, min), max)
  return `${widthInCh}ch`
}

function parseCaseTimestamp(value?: string | null): Date | null {
  if (!value) return null
  const date = new Date(value)
  return isValidDate(date) ? date : null
}

type DurationComponents = {
  years?: number
  months?: number
  weeks?: number
  days?: number
  hours?: number
  minutes?: number
  seconds?: number
}

const DURATION_COMPONENT_ORDER: Array<keyof DurationComponents> = [
  "years",
  "months",
  "weeks",
  "days",
  "hours",
  "minutes",
  "seconds",
]

const DURATION_SUFFIXES: Record<keyof DurationComponents, string> = {
  years: "y",
  months: "mo",
  weeks: "w",
  days: "d",
  hours: "h",
  minutes: "m",
  seconds: "s",
}

function formatDurationComponents(
  components: Partial<DurationComponents>
): string {
  const normalized: Required<DurationComponents> = {
    years: components.years ?? 0,
    months: components.months ?? 0,
    weeks: components.weeks ?? 0,
    days: components.days ?? 0,
    hours: components.hours ?? 0,
    minutes: components.minutes ?? 0,
    seconds: components.seconds ?? 0,
  }

  if (normalized.weeks) {
    normalized.days += normalized.weeks * 7
    normalized.weeks = 0
  }

  const parts: string[] = []
  for (const key of DURATION_COMPONENT_ORDER) {
    const value = normalized[key]
    if (!value) continue
    parts.push(`${value}${DURATION_SUFFIXES[key]}`)
  }
  return parts.length > 0 ? parts.join(" ") : "0s"
}

function formatIsoDurationCompact(duration?: string | null): string | null {
  if (!duration) return null
  try {
    const parsed = parseISODuration(duration)
    return formatDurationComponents(parsed)
  } catch (error) {
    console.error("Failed to parse ISO duration", error)
    return null
  }
}

function formatElapsedDuration(start: Date, end: Date): string {
  if (start >= end) return "0s"
  const elapsed = intervalToDuration({ start, end })
  return formatDurationComponents(elapsed)
}

function formatLocalDateTime(date: Date): string {
  return format(date, "MMM d yyyy '·' p")
}

function formatUtcDateTime(date: Date): string {
  return `${date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  })} UTC`
}

interface CaseDurationMetric {
  id: string
  name: string
  description?: string | null
  definitionId: string
  startedAt: Date
  endedAt: Date | null
  displayValue: string
  state: "ongoing" | "done"
}

interface CaseDurationMetricsProps {
  durations?: CaseDurationRead[]
  definitions?: CaseDurationDefinitionRead[]
  isLoading?: boolean
  variant?: "default" | "inline"
}

function CaseDurationMetrics({
  durations,
  definitions,
  isLoading = false,
  variant = "default",
}: CaseDurationMetricsProps) {
  const [now, setNow] = useState(() => new Date())
  const isInline = variant === "inline"

  const hasOngoingDuration = useMemo(
    () =>
      Boolean(
        durations?.some((duration) => duration.started_at && !duration.ended_at)
      ),
    [durations]
  )

  useEffect(() => {
    if (!hasOngoingDuration) {
      return
    }
    const interval = window.setInterval(() => {
      setNow(new Date())
    }, 1000)
    return () => window.clearInterval(interval)
  }, [hasOngoingDuration])

  const definitionById = useMemo(() => {
    if (!definitions || !definitions.length)
      return new Map<string, CaseDurationDefinitionRead>()
    return new Map(definitions.map((definition) => [definition.id, definition]))
  }, [definitions])

  const metrics = useMemo<CaseDurationMetric[]>(() => {
    if (!durations || durations.length === 0) return []

    return durations
      .map<CaseDurationMetric | null>((duration) => {
        const startedAt = parseCaseTimestamp(duration.started_at)
        if (!startedAt) return null

        const endedAt = parseCaseTimestamp(duration.ended_at)
        const definition = definitionById.get(duration.definition_id)
        const name =
          definition?.name ??
          `Duration ${duration.definition_id.slice(0, 8).toUpperCase()}`
        const description = definition?.description
        const state: CaseDurationMetric["state"] = endedAt ? "done" : "ongoing"

        const resolvedDuration =
          state === "done"
            ? (formatIsoDurationCompact(duration.duration) ??
              (endedAt ? formatElapsedDuration(startedAt, endedAt) : "—"))
            : formatElapsedDuration(startedAt, now)

        return {
          id: duration.id,
          name,
          description,
          definitionId: duration.definition_id,
          startedAt,
          endedAt,
          displayValue: resolvedDuration,
          state,
        }
      })
      .filter((item): item is CaseDurationMetric => item !== null)
  }, [definitionById, durations, now])

  if (isLoading && (!durations || durations.length === 0)) {
    if (isInline) {
      return <Skeleton className="h-4 w-24" />
    }

    return (
      <div className="py-1.5 first:pt-0 last:pb-0">
        <Skeleton className="h-6 w-32" />
      </div>
    )
  }

  if (metrics.length === 0) return null

  const metricsList = (
    <div
      className={`flex items-center gap-2 ${
        isInline ? "flex-nowrap shrink-0" : "flex-wrap"
      }`}
    >
      {metrics.map((metric) => {
        const IconComponent =
          metric.state === "ongoing" ? Hourglass : FlagTriangleRight
        const tooltipLabel =
          metric.state === "ongoing" ? "Ongoing" : "Completed"

        return (
          <HoverCard key={metric.id} openDelay={100} closeDelay={100}>
            <HoverCardTrigger asChild>
              <Badge
                variant="outline"
                className="min-w-0 gap-2 px-2 py-1 text-xs font-medium bg-background text-foreground"
              >
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex text-muted-foreground">
                      <IconComponent
                        aria-hidden="true"
                        className="h-3.5 w-3.5"
                      />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">
                    {tooltipLabel}
                  </TooltipContent>
                </Tooltip>
                <span className="max-w-[9rem] truncate">{metric.name}</span>
                <span className="font-mono text-muted-foreground">
                  {metric.displayValue}
                </span>
              </Badge>
            </HoverCardTrigger>
            <HoverCardContent className="w-80">
              <div className="flex flex-col gap-3">
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    {metric.name}
                  </p>
                  {metric.description ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {metric.description}
                    </p>
                  ) : null}
                </div>
                <div className="space-y-3 text-xs">
                  <div>
                    <p className="font-medium uppercase tracking-wide text-muted-foreground">
                      Start Event
                    </p>
                    <p className="mt-1">
                      Local: {formatLocalDateTime(metric.startedAt)}
                    </p>
                    <p className="text-muted-foreground">
                      UTC: {formatUtcDateTime(metric.startedAt)}
                    </p>
                  </div>
                  <div>
                    <p className="font-medium uppercase tracking-wide text-muted-foreground">
                      End Event
                    </p>
                    {metric.endedAt ? (
                      <>
                        <p className="mt-1">
                          Local: {formatLocalDateTime(metric.endedAt)}
                        </p>
                        <p className="text-muted-foreground">
                          UTC: {formatUtcDateTime(metric.endedAt)}
                        </p>
                      </>
                    ) : (
                      <p className="mt-1 text-muted-foreground">
                        Not triggered
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </HoverCardContent>
          </HoverCard>
        )
      })}
    </div>
  )

  const content = (
    <TooltipProvider delayDuration={150}>{metricsList}</TooltipProvider>
  )

  if (isInline) {
    return content
  }

  return <div className="py-1.5 first:pt-0 last:pb-0">{content}</div>
}

interface CasePanelContentProps {
  caseId: string
}

export function CasePanelView({ caseId }: CasePanelContentProps) {
  const workspaceId = useWorkspaceId()
  const { members } = useWorkspaceMembers(workspaceId)
  const router = useRouter()
  const searchParams = useSearchParams()
  const { isFeatureEnabled } = useFeatureFlag()
  const caseTasksEnabled = isFeatureEnabled("case-tasks")

  const { caseData, caseDataIsLoading, caseDataError } = useGetCase({
    caseId,
    workspaceId,
  })
  const { caseDurations, caseDurationsIsLoading, caseDurationsError } =
    useCaseDurations({
      caseId,
      workspaceId,
      enabled: isFeatureEnabled("case-durations"),
    })
  const {
    caseDurationDefinitions,
    caseDurationDefinitionsIsLoading,
    caseDurationDefinitionsError,
  } = useCaseDurationDefinitions(
    workspaceId,
    isFeatureEnabled("case-durations")
  )
  const { updateCase } = useUpdateCase({
    workspaceId,
    caseId,
  })
  const { addCaseTag } = useAddCaseTag({ caseId, workspaceId })
  const { removeCaseTag } = useRemoveCaseTag({ caseId, workspaceId })
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const { dropdownDefinitions } = useCaseDropdownDefinitions(workspaceId)
  const setDropdownValue = useSetCaseDropdownValue(workspaceId)
  const { toast } = useToast()
  const customFields = useMemo(
    () => (caseData?.fields ?? []).filter((field) => !field.reserved),
    [caseData?.fields]
  )
  useEffect(() => {
    if (caseDurationsError) {
      console.error("Failed to load case durations:", caseDurationsError)
    }
  }, [caseDurationsError])
  useEffect(() => {
    if (caseDurationDefinitionsError) {
      console.error(
        "Failed to load case duration definitions:",
        caseDurationDefinitionsError
      )
    }
  }, [caseDurationDefinitionsError])
  const [userAddedCustomFieldIds, setUserAddedCustomFieldIds] = useState<
    string[]
  >([])
  const [customFieldComboboxOpen, setCustomFieldComboboxOpen] = useState(false)
  const [customFieldSearch, setCustomFieldSearch] = useState("")
  const [customFieldWidths, setCustomFieldWidths] = useState<
    Record<string, string>
  >({})
  const [clearedCustomFieldIds, setClearedCustomFieldIds] = useState<string[]>(
    []
  )
  const nonEmptyCustomFieldIds = useMemo(
    () =>
      customFields
        .filter(
          (field) =>
            !isCustomFieldValueEmpty(field.value) &&
            !clearedCustomFieldIds.includes(field.id)
        )
        .map((field) => field.id),
    [customFields, clearedCustomFieldIds]
  )
  useEffect(() => {
    setUserAddedCustomFieldIds((prev) =>
      prev.filter((id) => customFields.some((field) => field.id === id))
    )
  }, [customFields])
  useEffect(() => {
    setCustomFieldWidths((prev) => {
      const next: Record<string, string> = {}
      customFields.forEach((field) => {
        next[field.id] = getCustomFieldInputWidth(field.value, field.type)
      })

      const changed =
        Object.keys(next).length !== Object.keys(prev).length ||
        Object.entries(next).some(([key, value]) => prev[key] !== value)

      return changed ? next : prev
    })
  }, [customFields])
  useEffect(() => {
    setClearedCustomFieldIds((prev) =>
      prev.filter((id) => {
        const field = customFields.find((item) => item.id === id)
        if (!field) return false
        return isCustomFieldValueEmpty(field.value)
      })
    )
  }, [customFields])
  const visibleCustomFieldIds = useMemo(() => {
    const set = new Set([...nonEmptyCustomFieldIds, ...userAddedCustomFieldIds])
    return customFields.map((field) => field.id).filter((id) => set.has(id))
  }, [customFields, nonEmptyCustomFieldIds, userAddedCustomFieldIds])
  const visibleCustomFields = useMemo(
    () =>
      customFields.filter((field) => visibleCustomFieldIds.includes(field.id)),
    [customFields, visibleCustomFieldIds]
  )
  const availableCustomFields = useMemo(
    () =>
      customFields.filter((field) => !visibleCustomFieldIds.includes(field.id)),
    [customFields, visibleCustomFieldIds]
  )
  const handleCustomFieldValueChange = useCallback(
    (fieldId: string, value: unknown) => {
      setCustomFieldWidths((prev) => {
        const fieldType = customFields.find(
          (field) => field.id === fieldId
        )?.type
        return {
          ...prev,
          [fieldId]: getCustomFieldInputWidth(value, fieldType),
        }
      })
    },
    [customFields]
  )
  const handleCustomFieldAdd = useCallback(
    (fieldId: string) => {
      const targetField = customFields.find((field) => field.id === fieldId)
      setClearedCustomFieldIds((prev) => prev.filter((id) => id !== fieldId))
      setUserAddedCustomFieldIds((prev) =>
        prev.includes(fieldId) ? prev : [...prev, fieldId]
      )
      setCustomFieldComboboxOpen(false)
      setCustomFieldSearch("")
      setCustomFieldWidths((prev) => ({
        ...prev,
        [fieldId]:
          prev[fieldId] ??
          getCustomFieldInputWidth(
            targetField?.value ?? null,
            targetField?.type
          ),
      }))
    },
    [customFields]
  )
  const handleCustomFieldClearAndHide = useCallback(
    async (field: CaseFieldRead) => {
      setClearedCustomFieldIds((prev) =>
        prev.includes(field.id) ? prev : [...prev, field.id]
      )
      setUserAddedCustomFieldIds((prev) => prev.filter((id) => id !== field.id))
      try {
        await updateCase({
          fields: {
            [field.id]: null,
          },
        })
        handleCustomFieldValueChange(field.id, null)
      } catch (error) {
        console.error("Failed to clear custom field:", error)
        setClearedCustomFieldIds((prev) => prev.filter((id) => id !== field.id))
        setUserAddedCustomFieldIds((prev) =>
          prev.includes(field.id) ? prev : [...prev, field.id]
        )
      }
    },
    [handleCustomFieldValueChange, updateCase]
  )
  const handleCustomFieldPopoverChange = useCallback((open: boolean) => {
    setCustomFieldComboboxOpen(open)
    if (!open) {
      setCustomFieldSearch("")
    }
  }, [])

  // Get active tab from URL query params, default to "comments"
  const activeTab = (
    searchParams &&
    ["comments", "activity", "attachments", "payload"].includes(
      searchParams.get("tab") || ""
    )
      ? (searchParams.get("tab") ?? "comments")
      : "comments"
  ) as CasePanelTab

  // Function to handle tab changes and update URL
  const handleTabChange = useCallback(
    (tab: string) => {
      router.push(`/workspaces/${workspaceId}/cases/${caseId}?tab=${tab}`)
    },
    [router, workspaceId, caseId]
  )
  const durationsAreLoading =
    caseDurationsIsLoading || caseDurationDefinitionsIsLoading

  if (caseDataIsLoading) {
    return (
      <div className="flex h-full flex-col space-y-4 p-4">
        <div className="flex items-center justify-between border-b p-4">
          <div className="flex items-center space-x-4">
            <Skeleton className="h-4 w-16" />
            <div className="flex items-center space-x-2">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-3 w-32" />
            </div>
          </div>
        </div>
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-[200px] w-full" />
        <div className="flex space-x-4">
          <Skeleton className="h-6 w-20" />
          <Skeleton className="h-6 w-20" />
          <Skeleton className="h-6 w-20" />
        </div>
      </div>
    )
  }
  if (caseDataError || !caseData) {
    return (
      <AlertNotification
        level="error"
        message={caseDataError?.message ?? "Error occurred loading case data"}
      />
    )
  }

  const handlePriorityChange = async (newPriority: CasePriority) => {
    const params = {
      priority: newPriority,
    }
    await updateCase(params)
  }

  const handleSeverityChange = async (newSeverity: CaseSeverity) => {
    const params = {
      severity: newSeverity,
    }
    await updateCase(params)
  }

  const handleAssigneeChange = async (newAssignee?: AssigneeInfo | null) => {
    const params: Partial<CaseUpdate> = {
      assignee_id: newAssignee?.id || null,
    }
    await updateCase(params)
  }

  const handleTagToggle = async (tagId: string, hasTag: boolean) => {
    try {
      if (hasTag) {
        // Remove tag
        await removeCaseTag(tagId)
      } else {
        // Add tag
        await addCaseTag({ tag_id: tagId })
      }
    } catch (error) {
      console.error("Failed to modify tag:", error)
      toast({
        title: "Error",
        description: `Failed to ${hasTag ? "remove" : "add"} tag ${hasTag ? "from" : "to"} case. Please try again.`,
        variant: "destructive",
      })
    }
  }

  return (
    <>
      <CaseWorkflowTrigger caseData={caseData} />
      <div className="h-full flex w-full">
        <div className="h-full w-full min-w-0 flex">
          {/* Main section */}
          <div className="flex-1 min-w-0">
            <div className="h-full overflow-auto min-w-0 bg-muted/20">
              <div className="border-b bg-background">
                <div className="flex h-11 items-center px-3">
                  <div className="flex flex-1 justify-center overflow-hidden">
                    <div className="flex h-full items-center gap-x-4 overflow-x-auto text-sm">
                      <PrioritySelect
                        priority={caseData.priority || "unknown"}
                        onValueChange={handlePriorityChange}
                      />
                      <SeveritySelect
                        severity={caseData.severity || "unknown"}
                        onValueChange={handleSeverityChange}
                      />
                      <AssigneeSelect
                        assignee={caseData.assignee}
                        workspaceMembers={members ?? []}
                        onValueChange={handleAssigneeChange}
                      />
                      {dropdownDefinitions?.map(
                        (def: CaseDropdownDefinitionRead) => {
                          const currentValue = caseData.dropdown_values?.find(
                            (dv) => dv.definition_id === def.id
                          )
                          return (
                            <CaseDropdownSelect
                              key={def.id}
                              definition={def}
                              currentValue={currentValue}
                              onValueChange={(optionId) =>
                                setDropdownValue.mutate({
                                  caseId: caseData.id,
                                  definitionId: def.id,
                                  optionId,
                                })
                              }
                            />
                          )
                        }
                      )}
                      <CaseDurationMetrics
                        durations={caseDurations}
                        definitions={caseDurationDefinitions}
                        isLoading={durationsAreLoading}
                        variant="inline"
                      />
                    </div>
                  </div>
                  <CaseWorkflowTriggerButton className="ml-3 shrink-0" />
                </div>
              </div>
              <div className="py-8 pb-24 px-6 max-w-4xl mx-auto">
                {/* Header with Chat Toggle */}
                <div className="mb-4">
                  <div className="flex flex-col">
                    <div className="py-1.5 first:pt-0 last:pb-0">
                      {/* Case Summary */}
                      <CasePanelSummary
                        caseData={caseData}
                        updateCase={updateCase}
                      />
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-3 py-1.5 first:pt-0 last:pb-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {caseData.tags?.length ? (
                          caseData.tags.map((tag) => (
                            <TagBadge key={tag.id} tag={tag} />
                          ))
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            No tags
                          </span>
                        )}
                      </div>
                      {caseTags && caseTags.length > 0 && (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                            >
                              <MoreHorizontal className="h-4 w-4" />
                              <span className="sr-only">Manage tags</span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="text-xs">
                            {caseTags.map((tag) => {
                              const hasTag = caseData.tags?.some(
                                (t) => t.id === tag.id
                              )
                              return (
                                <DropdownMenuCheckboxItem
                                  key={tag.id}
                                  className="text-xs"
                                  checked={hasTag}
                                  onClick={async (e) => {
                                    e.stopPropagation()
                                    await handleTagToggle(tag.id, !!hasTag)
                                  }}
                                >
                                  <div
                                    className="mr-2 flex size-2 rounded-full"
                                    style={{
                                      backgroundColor: tag.color || undefined,
                                    }}
                                  />
                                  <span>{tag.name}</span>
                                </DropdownMenuCheckboxItem>
                              )
                            })}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </div>
                    <div
                      className={`flex flex-col gap-3 py-1.5 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between`}
                    >
                      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 sm:flex-1 sm:min-w-0">
                        {visibleCustomFields.length > 0 ? (
                          visibleCustomFields.map((field) => {
                            const label = undoSlugify(field.id)
                            return (
                              <div
                                key={field.id}
                                className="flex items-center gap-2 text-xs"
                              >
                                <span className="text-muted-foreground">
                                  {label}
                                </span>
                                <CustomField
                                  customField={field}
                                  updateCase={updateCase}
                                  formClassName="inline-flex"
                                  inputClassName="text-xs"
                                  inputStyle={{
                                    width:
                                      customFieldWidths[field.id] ??
                                      getCustomFieldInputWidth(
                                        field.value,
                                        field.type
                                      ),
                                  }}
                                  onValueChange={handleCustomFieldValueChange}
                                />
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-5 w-5 text-muted-foreground hover:text-foreground"
                                  onClick={() =>
                                    handleCustomFieldClearAndHide(field)
                                  }
                                >
                                  <X className="h-3.5 w-3.5" />
                                  <span className="sr-only">
                                    Remove {label} field
                                  </span>
                                </Button>
                              </div>
                            )
                          })
                        ) : customFields.length === 0 ? (
                          <span className="text-xs text-muted-foreground">
                            No custom fields configured
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            No custom fields selected
                          </span>
                        )}
                      </div>
                      {customFields.length > 0 && (
                        <div className="flex shrink-0 items-start sm:self-start">
                          <Popover
                            open={customFieldComboboxOpen}
                            onOpenChange={handleCustomFieldPopoverChange}
                          >
                            <PopoverTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 w-6 p-0"
                                aria-expanded={customFieldComboboxOpen}
                                aria-haspopup="listbox"
                              >
                                <MoreHorizontal className="h-4 w-4" />
                                <span className="sr-only">
                                  Toggle custom fields menu
                                </span>
                              </Button>
                            </PopoverTrigger>
                            <PopoverContent
                              align="end"
                              className="w-64 p-0"
                              sideOffset={4}
                            >
                              <Command>
                                <CommandInput
                                  placeholder="Search fields..."
                                  value={customFieldSearch}
                                  onValueChange={setCustomFieldSearch}
                                />
                                <CommandList>
                                  {availableCustomFields.length > 0 ? (
                                    <CommandGroup heading="Hidden fields">
                                      {availableCustomFields.map((field) => (
                                        <CommandItem
                                          key={field.id}
                                          value={field.id}
                                          onSelect={(value) => {
                                            handleCustomFieldAdd(value)
                                          }}
                                        >
                                          {undoSlugify(field.id)}
                                        </CommandItem>
                                      ))}
                                    </CommandGroup>
                                  ) : (
                                    <div className="px-3 py-2 text-xs text-muted-foreground">
                                      No hidden fields
                                    </div>
                                  )}
                                  <CommandSeparator />
                                  <CommandGroup>
                                    <CommandItem
                                      value="__manage__"
                                      onSelect={() => {
                                        router.push(
                                          `/workspaces/${workspaceId}/cases/custom-fields`
                                        )
                                        setCustomFieldComboboxOpen(false)
                                        setCustomFieldSearch("")
                                      }}
                                    >
                                      Manage fields
                                    </CommandItem>
                                  </CommandGroup>
                                </CommandList>
                              </Command>
                            </PopoverContent>
                          </Popover>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* Description */}
                <div className="mb-4">
                  <CasePanelDescription
                    caseData={caseData}
                    updateCase={updateCase}
                  />
                </div>

                {/* Tasks Section */}
                {caseTasksEnabled && (
                  <div className="mb-6">
                    <CaseTasksSection
                      caseId={caseId}
                      workspaceId={workspaceId}
                      caseData={caseData}
                    />
                  </div>
                )}

                {/* Tabs using shadcn components */}
                <Tabs
                  value={activeTab}
                  onValueChange={handleTabChange}
                  className="mt-[4.5rem] w-full"
                >
                  <TabsList className="h-8 w-full justify-start rounded-none bg-transparent p-0">
                    <TabsTrigger
                      className="flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                      value="comments"
                    >
                      <MessageSquare className="mr-1.5 h-3.5 w-3.5" />
                      Comments
                    </TabsTrigger>
                    <TabsTrigger
                      className="ml-6 flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                      value="activity"
                    >
                      <Activity className="mr-1.5 h-3.5 w-3.5" />
                      Activity
                    </TabsTrigger>
                    <TabsTrigger
                      className="ml-6 flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                      value="attachments"
                    >
                      <Paperclip className="mr-1.5 h-3.5 w-3.5" />
                      Attachments
                    </TabsTrigger>
                    <TabsTrigger
                      className="ml-6 flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                      value="payload"
                    >
                      <Braces className="mr-1.5 h-3.5 w-3.5" />
                      Payload
                    </TabsTrigger>
                  </TabsList>
                  <Separator className="mt-0" />

                  <TabsContent value="comments" className="mt-4">
                    <CommentSection caseId={caseId} workspaceId={workspaceId} />
                  </TabsContent>

                  <TabsContent value="activity" className="mt-4">
                    <CaseFeed caseId={caseId} workspaceId={workspaceId} />
                  </TabsContent>

                  <TabsContent value="attachments" className="mt-4">
                    <CaseAttachmentsSection
                      caseId={caseId}
                      workspaceId={workspaceId}
                    />
                  </TabsContent>

                  <TabsContent value="payload" className="mt-4">
                    <CasePayloadSection caseData={caseData} />
                  </TabsContent>
                </Tabs>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
