"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import { Cross2Icon } from "@radix-ui/react-icons"
import { format, sub } from "date-fns"
import {
  CalendarClockIcon,
  CalendarIcon,
  Check,
  CheckCircleIcon,
  ChevronDown,
  ChevronLeftIcon,
  ChevronRightIcon,
  CircleHelpIcon,
  CirclePauseIcon,
  Clock3Icon,
  FlagTriangleRightIcon,
  Minus,
  SearchIcon,
  SignalIcon,
  TrafficConeIcon,
  UserIcon,
  WebhookIcon,
} from "lucide-react"
import { type ComponentType, useEffect, useMemo, useRef, useState } from "react"
import type { DateRange } from "react-day-picker"
import type {
  TriggerType,
  WorkflowExecutionReadMinimal,
  WorkflowExecutionRelationFilter,
} from "@/client"
import {
  type FilterMode,
  FilterMultiSelect,
} from "@/components/cases/cases-header"
import { WorkflowExecutionStatusIcon } from "@/components/executions/nav"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import {
  groupWorkflowRunsByStatus,
  useWorkflowExecutionResetPoints,
  useWorkflowRunMutations,
  useWorkflowRuns,
} from "@/hooks/use-workflow-runs"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

import { ResetWorkflowRunDialog } from "./reset-workflow-run-dialog"
import { WorkflowRunItem } from "./workflow-run-item"

type RunStatus = WorkflowExecutionReadMinimal["status"]
type GroupStatus = RunStatus | "UNKNOWN"

function isRunStatus(status: GroupStatus): status is RunStatus {
  return status !== "UNKNOWN"
}

const STATUS_GROUPS: Array<{
  key: GroupStatus
  label: string
  iconClassName: string
  panelClassName: string
}> = [
  {
    key: "RUNNING",
    label: "Running",
    iconClassName: "text-blue-600",
    panelClassName:
      "data-[state=open]:border-l-blue-600 data-[state=open]:bg-blue-600/[0.03] dark:data-[state=open]:bg-blue-600/[0.08]",
  },
  {
    key: "FAILED",
    label: "Failed",
    iconClassName: "text-rose-600",
    panelClassName:
      "data-[state=open]:border-l-rose-600 data-[state=open]:bg-rose-600/[0.03] dark:data-[state=open]:bg-rose-600/[0.08]",
  },
  {
    key: "CANCELED",
    label: "Canceled",
    iconClassName: "text-orange-600",
    panelClassName:
      "data-[state=open]:border-l-orange-600 data-[state=open]:bg-orange-600/[0.03] dark:data-[state=open]:bg-orange-600/[0.08]",
  },
  {
    key: "TERMINATED",
    label: "Terminated",
    iconClassName: "text-rose-600",
    panelClassName:
      "data-[state=open]:border-l-rose-600 data-[state=open]:bg-rose-600/[0.03] dark:data-[state=open]:bg-rose-600/[0.08]",
  },
  {
    key: "TIMED_OUT",
    label: "Timed out",
    iconClassName: "text-amber-600",
    panelClassName:
      "data-[state=open]:border-l-amber-600 data-[state=open]:bg-amber-600/[0.03] dark:data-[state=open]:bg-amber-600/[0.08]",
  },
  {
    key: "COMPLETED",
    label: "Completed",
    iconClassName: "text-emerald-600",
    panelClassName:
      "data-[state=open]:border-l-emerald-600 data-[state=open]:bg-emerald-600/[0.03] dark:data-[state=open]:bg-emerald-600/[0.08]",
  },
  {
    key: "CONTINUED_AS_NEW",
    label: "Continued as new",
    iconClassName: "text-violet-600",
    panelClassName:
      "data-[state=open]:border-l-violet-600 data-[state=open]:bg-violet-600/[0.03] dark:data-[state=open]:bg-violet-600/[0.08]",
  },
  {
    key: "UNKNOWN",
    label: "Unknown",
    iconClassName: "text-muted-foreground",
    panelClassName:
      "data-[state=open]:border-l-muted-foreground data-[state=open]:bg-muted/50",
  },
]

const RELATION_OPTIONS: Array<{
  value: WorkflowExecutionRelationFilter
  label: string
}> = [
  { value: "all", label: "All" },
  { value: "root", label: "Root" },
  { value: "child", label: "Child" },
]
const LIMIT_OPTIONS = [50, 100, 250, 500]

const STATUS_FILTER_OPTIONS: Array<{
  value: RunStatus
  label: string
  icon: ComponentType<{ className?: string }>
  iconClassName: string
}> = [
  {
    value: "RUNNING",
    label: "Running",
    icon: FlagTriangleRightIcon,
    iconClassName: "text-blue-600",
  },
  {
    value: "FAILED",
    label: "Failed",
    icon: TrafficConeIcon,
    iconClassName: "text-rose-600",
  },
  {
    value: "CANCELED",
    label: "Canceled",
    icon: CirclePauseIcon,
    iconClassName: "text-orange-600",
  },
  {
    value: "TERMINATED",
    label: "Terminated",
    icon: CirclePauseIcon,
    iconClassName: "text-rose-600",
  },
  {
    value: "TIMED_OUT",
    label: "Timed out",
    icon: CirclePauseIcon,
    iconClassName: "text-amber-600",
  },
  {
    value: "COMPLETED",
    label: "Completed",
    icon: CheckCircleIcon,
    iconClassName: "text-emerald-600",
  },
  {
    value: "CONTINUED_AS_NEW",
    label: "Continued as new",
    icon: SignalIcon,
    iconClassName: "text-violet-600",
  },
]

const TRIGGER_FILTER_OPTIONS: Array<{
  value: TriggerType
  label: string
  icon: ComponentType<{ className?: string }>
  iconClassName: string
}> = [
  {
    value: "manual",
    label: "Manual",
    icon: FlagTriangleRightIcon,
    iconClassName: "text-blue-600",
  },
  {
    value: "scheduled",
    label: "Scheduled",
    icon: CalendarClockIcon,
    iconClassName: "text-emerald-600",
  },
  {
    value: "webhook",
    label: "Webhook",
    icon: WebhookIcon,
    iconClassName: "text-violet-600",
  },
  {
    value: "case",
    label: "Case",
    icon: SignalIcon,
    iconClassName: "text-orange-600",
  },
]

type TimeFilterOperator = "before" | "between" | "after"
type TimeFilterMode = "absolute" | "relative"
type RelativeTimeUnit = "minutes" | "hours" | "days" | "weeks" | "months"

interface TimeFilterState {
  operator: TimeFilterOperator
  mode: TimeFilterMode
  from: Date | null
  to: Date | null
  relativeValue: string
  relativeUnit: RelativeTimeUnit
}

function createDefaultTimeFilterState(): TimeFilterState {
  return {
    operator: "between",
    mode: "absolute",
    from: null,
    to: null,
    relativeValue: "",
    relativeUnit: "hours",
  }
}

const TIME_FILTER_OPERATOR_OPTIONS: Array<{
  value: TimeFilterOperator
  label: string
}> = [
  { value: "before", label: "Before" },
  { value: "between", label: "Between" },
  { value: "after", label: "After" },
]

const RELATIVE_TIME_UNIT_OPTIONS: RelativeTimeUnit[] = [
  "minutes",
  "hours",
  "days",
  "weeks",
  "months",
]

function cloneTimeFilterState(value: TimeFilterState): TimeFilterState {
  return {
    ...value,
    from: value.from ? new Date(value.from) : null,
    to: value.to ? new Date(value.to) : null,
  }
}

function formatTimeInputValue(value: Date | null): string {
  if (!value) {
    return ""
  }
  return format(value, "HH:mm:ss")
}

function applyTimeInput(value: Date | null, time: string): Date | null {
  if (!value) {
    return null
  }
  const [hoursText = "", minutesText = "", secondsText = "0"] = time.split(":")
  const hours = Number.parseInt(hoursText, 10)
  const minutes = Number.parseInt(minutesText, 10)
  const seconds = Number.parseInt(secondsText, 10)
  if (Number.isNaN(hours) || Number.isNaN(minutes) || Number.isNaN(seconds)) {
    return value
  }
  const next = new Date(value)
  next.setHours(hours, minutes, seconds, 0)
  return next
}

function applyDateInput(
  selectedDate: Date | null,
  currentValue: Date | null
): Date | null {
  if (!selectedDate) {
    return null
  }
  const next = new Date(selectedDate)
  if (currentValue) {
    next.setHours(
      currentValue.getHours(),
      currentValue.getMinutes(),
      currentValue.getSeconds(),
      currentValue.getMilliseconds()
    )
  }
  return next
}

function relativeDurationToDate(
  relativeValue: string,
  relativeUnit: RelativeTimeUnit
): Date | null {
  const amount = Number.parseInt(relativeValue, 10)
  if (!Number.isFinite(amount) || amount <= 0) {
    return null
  }

  switch (relativeUnit) {
    case "minutes":
      return sub(new Date(), { minutes: amount })
    case "hours":
      return sub(new Date(), { hours: amount })
    case "days":
      return sub(new Date(), { days: amount })
    case "weeks":
      return sub(new Date(), { weeks: amount })
    case "months":
      return sub(new Date(), { months: amount })
    default:
      return null
  }
}

function formatRelativeTimeLabel(
  relativeValue: string,
  relativeUnit: RelativeTimeUnit
): string | null {
  const amount = Number.parseInt(relativeValue, 10)
  if (!Number.isFinite(amount) || amount <= 0) {
    return null
  }
  const unit = amount === 1 ? relativeUnit.slice(0, -1) : relativeUnit
  return `${amount} ${unit} ago`
}

function formatRelativeUnitLabel(
  relativeUnit: RelativeTimeUnit,
  relativeValue?: string
): string {
  const amount = Number.parseInt(relativeValue ?? "", 10)
  const unit =
    Number.isFinite(amount) && amount === 1
      ? relativeUnit.slice(0, -1)
      : relativeUnit
  return `${unit} ago`
}

function normalizeTimeFilterForQuery(value: TimeFilterState): {
  from: string | null
  to: string | null
} {
  if (value.operator === "between") {
    if (!value.from || !value.to) {
      return { from: null, to: null }
    }
    const start = value.from
    const end = value.to
    const [normalizedFrom, normalizedTo] =
      start.getTime() <= end.getTime() ? [start, end] : [end, start]
    return {
      from: normalizedFrom.toISOString(),
      to: normalizedTo.toISOString(),
    }
  }

  const anchorDate =
    value.mode === "relative"
      ? relativeDurationToDate(value.relativeValue, value.relativeUnit)
      : value.from
  if (!anchorDate) {
    return { from: null, to: null }
  }

  if (value.operator === "after") {
    return { from: anchorDate.toISOString(), to: null }
  }

  return {
    from: null,
    to: anchorDate.toISOString(),
  }
}

function isTimeFilterActive(value: TimeFilterState): boolean {
  const normalized = normalizeTimeFilterForQuery(value)
  return normalized.from !== null || normalized.to !== null
}

function formatTimeFilterLabel(value: TimeFilterState): string | null {
  if (value.operator === "between") {
    if (!value.from || !value.to) {
      return null
    }
    const toDate = value.to
    const [normalizedFrom, normalizedTo] =
      value.from.getTime() <= toDate.getTime()
        ? [value.from, toDate]
        : [toDate, value.from]
    return `${format(normalizedFrom, "MMM d, HH:mm")} - ${format(
      normalizedTo,
      "MMM d, HH:mm"
    )}`
  }

  const prefix = value.operator === "after" ? "After " : "Before "

  if (value.mode === "relative") {
    const label = formatRelativeTimeLabel(
      value.relativeValue,
      value.relativeUnit
    )
    return label ? `${prefix}${label}` : null
  }

  if (!value.from) {
    return null
  }
  return `${prefix}${format(value.from, "MMM d, HH:mm")}`
}

function getTimeFilterValidationMessage(value: TimeFilterState): string | null {
  if (value.operator === "between") {
    if (!value.from || !value.to) {
      return "Select a start and end time."
    }
    if (value.to.getTime() < value.from.getTime()) {
      return "End time must be after start time."
    }
    return null
  }

  if (value.mode === "relative") {
    if (
      relativeDurationToDate(value.relativeValue, value.relativeUnit) === null
    ) {
      return "Enter a positive relative duration."
    }
    return null
  }

  if (!value.from) {
    return "Select a time."
  }
  return null
}

interface TimeFilterDateTimeFieldProps {
  label: string
  value: Date | null
  onDateChange: (value: Date | null) => void
  onTimeChange: (time: string) => void
}

interface TimeInputFieldProps {
  label: string
  value: Date | null
  disabled: boolean
  onChange: (time: string) => void
}

function TimeInputField({
  label,
  value,
  disabled,
  onChange,
}: TimeInputFieldProps) {
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
      <div className="relative">
        <Input
          type="time"
          step={1}
          value={formatTimeInputValue(value)}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          className="h-8 pr-8 text-xs appearance-none [&::-webkit-calendar-picker-indicator]:hidden [&::-webkit-calendar-picker-indicator]:appearance-none"
        />
        <Clock3Icon className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
      </div>
    </div>
  )
}

function TimeFilterDateTimeField({
  label,
  value,
  onDateChange,
  onTimeChange,
}: TimeFilterDateTimeFieldProps) {
  const normalizedTimeLabel = /time/i.test(label) ? label : `${label} time`

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium">{label}</p>
      <div className="overflow-hidden rounded-md">
        <Calendar
          mode="single"
          selected={value ?? undefined}
          onSelect={(date) => onDateChange(date ?? null)}
          className="p-2"
        />
        <div className="bg-card p-2">
          <TimeInputField
            label={normalizedTimeLabel}
            value={value}
            disabled={!value}
            onChange={onTimeChange}
          />
        </div>
      </div>
    </div>
  )
}

interface TimeFilterDateRangeFieldProps {
  from: Date | null
  to: Date | null
  onRangeChange: (range: DateRange | undefined) => void
  onFromTimeChange: (time: string) => void
  onToTimeChange: (time: string) => void
}

function TimeFilterDateRangeField({
  from,
  to,
  onRangeChange,
  onFromTimeChange,
  onToTimeChange,
}: TimeFilterDateRangeFieldProps) {
  const selectedRange = useMemo(
    () =>
      from
        ? {
            from,
            to: to ?? undefined,
          }
        : undefined,
    [from, to]
  )

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium">Date range</p>
      <div className="overflow-hidden rounded-md">
        <Calendar
          mode="range"
          selected={selectedRange}
          onSelect={onRangeChange}
          numberOfMonths={2}
          className="p-2"
        />
        <div className="grid grid-cols-2 gap-2 bg-card p-2">
          <TimeInputField
            label="Start time"
            value={from}
            disabled={!from}
            onChange={onFromTimeChange}
          />
          <TimeInputField
            label="End time"
            value={to}
            disabled={!to}
            onChange={onToTimeChange}
          />
        </div>
      </div>
    </div>
  )
}

interface TimeFilterButtonProps {
  label: string
  value: TimeFilterState
  onChange: (value: TimeFilterState) => void
}

function TimeFilterButton({ label, value, onChange }: TimeFilterButtonProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<TimeFilterState>(() =>
    cloneTimeFilterState(value)
  )
  const [attemptedApply, setAttemptedApply] = useState(false)

  useEffect(() => {
    if (!open) {
      setDraft(cloneTimeFilterState(value))
    }
  }, [value, open])

  const summaryLabel = formatTimeFilterLabel(value)
  const validationMessage = getTimeFilterValidationMessage(draft)
  const visibleValidationMessage =
    attemptedApply && validationMessage ? validationMessage : null
  const relativeUnitLabel = formatRelativeUnitLabel(
    draft.relativeUnit,
    draft.relativeValue
  )

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen)
    setAttemptedApply(false)
    if (nextOpen) {
      setDraft(cloneTimeFilterState(value))
    }
  }

  const handleApply = () => {
    if (validationMessage) {
      setAttemptedApply(true)
      return
    }
    onChange(cloneTimeFilterState(draft))
    setAttemptedApply(false)
    setOpen(false)
  }

  const handleClear = () => {
    const cleared = createDefaultTimeFilterState()
    onChange(cleared)
    setDraft(cleared)
    setAttemptedApply(false)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors",
            "hover:bg-muted/50",
            summaryLabel && "border-primary/50 bg-primary/5"
          )}
        >
          <CalendarIcon className="size-3.5 text-muted-foreground" />
          <span>{label}</span>
          {summaryLabel ? (
            <span className="ml-0.5 max-w-[120px] truncate text-[10px] text-muted-foreground">
              {summaryLabel}
            </span>
          ) : null}
          <ChevronDown className="size-3 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto max-w-[95vw] p-3"
        align="start"
        sideOffset={4}
      >
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-[11px] font-medium text-muted-foreground">
              Condition
            </p>
            <div className="inline-flex w-full rounded-md border border-input p-0.5">
              {TIME_FILTER_OPERATOR_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={cn(
                    "flex-1 rounded px-2 py-1 text-xs transition-colors",
                    draft.operator === option.value
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  onClick={() =>
                    setDraft((previous) => ({
                      ...previous,
                      operator: option.value,
                    }))
                  }
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {draft.operator === "between" ? (
            <TimeFilterDateRangeField
              from={draft.from}
              to={draft.to}
              onRangeChange={(range) =>
                setDraft((previous) => ({
                  ...previous,
                  from: range?.from
                    ? applyDateInput(range.from, previous.from)
                    : null,
                  to: range?.to ? applyDateInput(range.to, previous.to) : null,
                }))
              }
              onFromTimeChange={(time) =>
                setDraft((previous) => ({
                  ...previous,
                  from: applyTimeInput(previous.from, time),
                }))
              }
              onToTimeChange={(time) =>
                setDraft((previous) => ({
                  ...previous,
                  to: applyTimeInput(previous.to, time),
                }))
              }
            />
          ) : (
            <>
              <div className="space-y-1">
                <p className="text-[11px] font-medium text-muted-foreground">
                  Mode
                </p>
                <div className="inline-flex w-full rounded-md border border-input p-0.5">
                  {(["relative", "absolute"] as TimeFilterMode[]).map(
                    (mode) => (
                      <button
                        key={mode}
                        type="button"
                        className={cn(
                          "flex-1 rounded px-2 py-1 text-xs capitalize transition-colors",
                          draft.mode === mode
                            ? "bg-muted text-foreground"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                        onClick={() =>
                          setDraft((previous) => ({
                            ...previous,
                            mode,
                          }))
                        }
                      >
                        {mode}
                      </button>
                    )
                  )}
                </div>
              </div>

              {draft.mode === "relative" ? (
                <div className="space-y-2">
                  <p className="text-xs font-medium">Relative time</p>
                  <div className="flex items-center gap-2">
                    <Input
                      type="number"
                      min={1}
                      value={draft.relativeValue}
                      placeholder="e.g. 24"
                      onChange={(event) =>
                        setDraft((previous) => ({
                          ...previous,
                          relativeValue: event.target.value,
                        }))
                      }
                      className="h-8 text-xs"
                    />
                    <Select
                      value={draft.relativeUnit}
                      onValueChange={(nextValue) =>
                        setDraft((previous) => ({
                          ...previous,
                          relativeUnit: nextValue as RelativeTimeUnit,
                        }))
                      }
                    >
                      <SelectTrigger className="h-8 w-[130px] text-xs">
                        <SelectValue>{relativeUnitLabel}</SelectValue>
                      </SelectTrigger>
                      <SelectContent align="start">
                        {RELATIVE_TIME_UNIT_OPTIONS.map((unit) => (
                          <SelectItem key={unit} value={unit}>
                            {formatRelativeUnitLabel(unit)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              ) : (
                <TimeFilterDateTimeField
                  label="Time"
                  value={draft.from}
                  onDateChange={(date) =>
                    setDraft((previous) => ({
                      ...previous,
                      from: applyDateInput(date, previous.from),
                    }))
                  }
                  onTimeChange={(time) =>
                    setDraft((previous) => ({
                      ...previous,
                      from: applyTimeInput(previous.from, time),
                    }))
                  }
                />
              )}
            </>
          )}

          {visibleValidationMessage ? (
            <p className="text-[11px] text-destructive">
              {visibleValidationMessage}
            </p>
          ) : null}

          <div className="flex items-center justify-end gap-2 pt-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
              onClick={handleClear}
            >
              Clear
              <Cross2Icon className="size-3" />
            </Button>
            <Button
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={handleApply}
            >
              Apply
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

function formatDurationValue(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m`
  }
  return `${Math.floor(seconds / 3600)}h`
}

function formatDurationRangeLabel(
  minSeconds: number | null,
  maxSeconds: number | null
): string | null {
  if (minSeconds === null && maxSeconds === null) {
    return null
  }
  if (minSeconds !== null && maxSeconds !== null) {
    return `${formatDurationValue(minSeconds)} - ${formatDurationValue(maxSeconds)}`
  }
  if (minSeconds !== null) {
    return `>= ${formatDurationValue(minSeconds)}`
  }
  return `<= ${formatDurationValue(maxSeconds ?? 0)}`
}

interface DurationFilterButtonProps {
  minSeconds: number | null
  maxSeconds: number | null
  onChange: (next: {
    minSeconds: number | null
    maxSeconds: number | null
  }) => void
}

function DurationFilterButton({
  minSeconds,
  maxSeconds,
  onChange,
}: DurationFilterButtonProps) {
  const durationLabel = formatDurationRangeLabel(minSeconds, maxSeconds)
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors",
            "hover:bg-muted/50",
            durationLabel && "border-primary/50 bg-primary/5"
          )}
        >
          <Clock3Icon className="size-3.5 text-muted-foreground" />
          <span>Duration</span>
          {durationLabel ? (
            <span className="ml-0.5 max-w-[120px] truncate text-[10px] text-muted-foreground">
              {durationLabel}
            </span>
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[280px] p-3" align="start" sideOffset={4}>
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-xs font-medium">Minimum (seconds)</p>
            <Input
              type="number"
              min={0}
              value={minSeconds ?? ""}
              onChange={(event) => {
                const value = event.target.value
                if (value === "") {
                  onChange({ minSeconds: null, maxSeconds })
                  return
                }
                const parsed = Number.parseInt(value, 10)
                onChange({
                  minSeconds: Number.isNaN(parsed) ? null : Math.max(parsed, 0),
                  maxSeconds,
                })
              }}
              className="h-8 text-xs"
            />
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium">Maximum (seconds)</p>
            <Input
              type="number"
              min={0}
              value={maxSeconds ?? ""}
              onChange={(event) => {
                const value = event.target.value
                if (value === "") {
                  onChange({ minSeconds, maxSeconds: null })
                  return
                }
                const parsed = Number.parseInt(value, 10)
                onChange({
                  minSeconds,
                  maxSeconds: Number.isNaN(parsed) ? null : Math.max(parsed, 0),
                })
              }}
              className="h-8 text-xs"
            />
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
            onClick={() => onChange({ minSeconds: null, maxSeconds: null })}
          >
            Clear
            <Cross2Icon className="size-3" />
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

export function WorkflowRunsLayout() {
  const workspaceId = useWorkspaceId()
  const { members } = useWorkspaceMembers(workspaceId)
  const [limit, setLimit] = useState(50)
  const [searchTerm, setSearchTerm] = useState("")
  const [relation, setRelation] =
    useState<WorkflowExecutionRelationFilter>("all")
  const [statusFilter, setStatusFilter] = useState<RunStatus[]>([])
  const [statusMode, setStatusMode] = useState<FilterMode>("include")
  const [triggerFilter, setTriggerFilter] = useState<TriggerType[]>([])
  const [userFilter, setUserFilter] = useState<string[]>([])
  const [startTimeFilter, setStartTimeFilter] = useState<TimeFilterState>(() =>
    createDefaultTimeFilterState()
  )
  const [closeTimeFilter, setCloseTimeFilter] = useState<TimeFilterState>(() =>
    createDefaultTimeFilterState()
  )
  const [durationMinSeconds, setDurationMinSeconds] = useState<number | null>(
    null
  )
  const [durationMaxSeconds, setDurationMaxSeconds] = useState<number | null>(
    null
  )
  const [selectedExecutionIds, setSelectedExecutionIds] = useState<Set<string>>(
    new Set()
  )
  const [groupSelectionAnchorIndex, setGroupSelectionAnchorIndex] = useState<
    number | null
  >(null)
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [bulkCancelConfirmOpen, setBulkCancelConfirmOpen] = useState(false)
  const [bulkTerminateConfirmOpen, setBulkTerminateConfirmOpen] =
    useState(false)
  const [resetTargetExecutionIds, setResetTargetExecutionIds] = useState<
    string[]
  >([])
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const startRange = normalizeTimeFilterForQuery(startTimeFilter)
  const closeRange = normalizeTimeFilterForQuery(closeTimeFilter)

  const {
    runs,
    hasNextPage,
    hasPreviousPage,
    goToNextPage,
    goToPreviousPage,
    currentPage,
    startItem,
    endItem,
    isLoading,
    error,
  } = useWorkflowRuns(
    {
      searchTerm,
      relation,
      status: statusFilter,
      statusMode,
      trigger: triggerFilter,
      userId: userFilter[0] ?? undefined,
      startTimeFrom: startRange.from,
      startTimeTo: startRange.to,
      closeTimeFrom: closeRange.from,
      closeTimeTo: closeRange.to,
      durationGteSeconds: durationMinSeconds,
      durationLteSeconds: durationMaxSeconds,
    },
    {
      limit,
    }
  )

  const {
    cancelRun,
    terminateRun,
    bulkCancelRuns,
    bulkTerminateRuns,
    resetRun,
    bulkResetRuns,
    isCanceling,
    isTerminating,
    isBulkCanceling,
    isBulkTerminating,
    isResetting,
    isBulkResetting,
  } = useWorkflowRunMutations()
  const singleResetExecutionId =
    resetTargetExecutionIds.length === 1 ? resetTargetExecutionIds[0] : null
  const { data: resetPoints, isLoading: resetPointsLoading } =
    useWorkflowExecutionResetPoints(singleResetExecutionId, {
      enabled: resetDialogOpen && singleResetExecutionId !== null,
    })

  const groupedRuns = useMemo(() => groupWorkflowRunsByStatus(runs), [runs])
  const filteredGroups = useMemo(
    () =>
      STATUS_GROUPS.filter(
        (group) => (groupedRuns[group.key]?.length ?? 0) > 0
      ),
    [groupedRuns]
  )
  const userFilterOptions = useMemo(
    () =>
      (members ?? []).map((member) => ({
        value: member.user_id,
        label: getDisplayName({
          first_name: member.first_name,
          last_name: member.last_name,
          email: member.email,
        }),
        icon: UserIcon,
        iconClassName: "text-muted-foreground",
      })),
    [members]
  )

  const hasFilters =
    searchTerm.trim().length > 0 ||
    relation !== "all" ||
    statusFilter.length > 0 ||
    statusMode !== "include" ||
    triggerFilter.length > 0 ||
    userFilter.length > 0 ||
    isTimeFilterActive(startTimeFilter) ||
    isTimeFilterActive(closeTimeFilter) ||
    durationMinSeconds !== null ||
    durationMaxSeconds !== null

  const selectedCount = selectedExecutionIds.size
  const selectedRunningExecutionIds = useMemo(
    () =>
      runs
        .filter(
          (run) => selectedExecutionIds.has(run.id) && run.status === "RUNNING"
        )
        .map((run) => run.id),
    [runs, selectedExecutionIds]
  )
  const selectedRunningCount = selectedRunningExecutionIds.length
  const allVisibleSelected =
    runs.length > 0 && runs.every((run) => selectedExecutionIds.has(run.id))
  const showingLabel = useMemo(() => {
    if (runs.length === 0 || startItem === 0 || endItem === 0) {
      return null
    }
    return `Showing ${startItem}-${endItem}`
  }, [endItem, runs.length, startItem])
  const showPaginationControls =
    runs.length > 0 || hasNextPage || hasPreviousPage

  useEffect(() => {
    setSelectedExecutionIds(new Set())
    setGroupSelectionAnchorIndex(null)
  }, [
    searchTerm,
    relation,
    statusFilter,
    statusMode,
    triggerFilter,
    userFilter,
    startTimeFilter,
    closeTimeFilter,
    durationMinSeconds,
    durationMaxSeconds,
    currentPage,
    limit,
  ])

  const handleSelectAllVisible = () => {
    if (allVisibleSelected) {
      setSelectedExecutionIds(new Set())
      return
    }
    setSelectedExecutionIds(new Set(runs.map((run) => run.id)))
  }

  const handleToggleRunSelection = (executionId: string, checked: boolean) => {
    setSelectedExecutionIds((previous) => {
      const next = new Set(previous)
      if (checked) {
        next.add(executionId)
      } else {
        next.delete(executionId)
      }
      return next
    })
  }

  const handleToggleGroupSelection = ({
    groupIndex,
    executionIds,
    checked,
    shiftKey,
    additive,
  }: {
    groupIndex: number
    executionIds: string[]
    checked: boolean
    shiftKey: boolean
    additive: boolean
  }) => {
    const hasRangeSelection =
      shiftKey &&
      groupSelectionAnchorIndex !== null &&
      groupSelectionAnchorIndex !== groupIndex

    const selectionTargetIds = hasRangeSelection
      ? filteredGroups
          .slice(
            Math.min(groupSelectionAnchorIndex, groupIndex),
            Math.max(groupSelectionAnchorIndex, groupIndex) + 1
          )
          .flatMap((group) =>
            (groupedRuns[group.key] ?? []).map((run) => run.id)
          )
      : executionIds

    setSelectedExecutionIds((previous) => {
      const shouldReplace = !additive
      const next = shouldReplace ? new Set<string>() : new Set(previous)
      for (const executionId of selectionTargetIds) {
        if (checked) {
          next.add(executionId)
        } else {
          next.delete(executionId)
        }
      }
      return next
    })
    setGroupSelectionAnchorIndex(groupIndex)
  }

  const openSingleResetDialog = (executionId: string) => {
    setResetTargetExecutionIds([executionId])
    setResetDialogOpen(true)
  }

  const openBulkResetDialog = () => {
    if (selectedExecutionIds.size === 0) {
      return
    }
    setResetTargetExecutionIds(Array.from(selectedExecutionIds))
    setResetDialogOpen(true)
  }

  const handleCancelRun = async (executionId: string) => {
    try {
      await cancelRun(executionId)
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to cancel run",
        description: e instanceof Error ? e.message : "Unknown error",
      })
    }
  }

  const handleTerminateRun = async (executionId: string) => {
    try {
      await terminateRun({ executionId })
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to terminate run",
        description: e instanceof Error ? e.message : "Unknown error",
      })
    }
  }

  const handleResetSubmit = async (input: {
    eventId?: number | null
    reason?: string | null
    reapplyType: "all_eligible" | "signal_only" | "none"
  }) => {
    try {
      if (resetTargetExecutionIds.length === 1) {
        await resetRun({
          executionId: resetTargetExecutionIds[0],
          eventId: input.eventId,
          reason: input.reason,
          reapplyType: input.reapplyType,
        })
      } else {
        await bulkResetRuns({
          executionIds: resetTargetExecutionIds,
          eventId: input.eventId,
          reason: input.reason,
          reapplyType: input.reapplyType,
        })
      }
      setSelectedExecutionIds(new Set())
      setResetTargetExecutionIds([])
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to reset run",
        description: e instanceof Error ? e.message : "Unknown error",
      })
    }
  }

  const handleBulkCancelSelected = async () => {
    if (selectedRunningExecutionIds.length === 0) {
      setBulkCancelConfirmOpen(false)
      return
    }
    try {
      await bulkCancelRuns({ executionIds: selectedRunningExecutionIds })
      setSelectedExecutionIds(new Set())
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to cancel selected runs",
        description: e instanceof Error ? e.message : "Unknown error",
      })
    } finally {
      setBulkCancelConfirmOpen(false)
    }
  }

  const handleBulkTerminateSelected = async () => {
    if (selectedRunningExecutionIds.length === 0) {
      setBulkTerminateConfirmOpen(false)
      return
    }
    try {
      await bulkTerminateRuns({ executionIds: selectedRunningExecutionIds })
      setSelectedExecutionIds(new Set())
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to terminate selected runs",
        description: e instanceof Error ? e.message : "Unknown error",
      })
    } finally {
      setBulkTerminateConfirmOpen(false)
    }
  }

  const resetFilters = () => {
    setSearchTerm("")
    setRelation("all")
    setStatusFilter([])
    setStatusMode("include")
    setTriggerFilter([])
    setUserFilter([])
    setStartTimeFilter(createDefaultTimeFilterState())
    setCloseTimeFilter(createDefaultTimeFilterState())
    setDurationMinSeconds(null)
    setDurationMaxSeconds(null)
  }

  const isMutating =
    isCanceling ||
    isTerminating ||
    isBulkCanceling ||
    isBulkTerminating ||
    isResetting ||
    isBulkResetting

  if (error) {
    return <AlertNotification message={error.message} />
  }

  return (
    <div className="flex size-full flex-col">
      <div className="shrink-0 border-b">
        <header className="flex h-10 items-center border-b pl-3 pr-4">
          <div
            className="flex min-w-0 flex-1 items-center gap-3"
            onClick={() => searchInputRef.current?.focus()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                searchInputRef.current?.focus()
              }
            }}
            role="button"
            tabIndex={0}
            aria-label="Focus runs search"
          >
            <div className="flex h-7 w-7 shrink-0 items-center justify-center">
              <SearchIcon className="size-4 text-muted-foreground" />
            </div>
            <Input
              ref={searchInputRef}
              type="text"
              placeholder="Search runs..."
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              className={cn(
                "h-7 w-full border-none bg-transparent p-0",
                "text-sm",
                "shadow-none outline-none",
                "placeholder:text-muted-foreground",
                "focus-visible:ring-0 focus-visible:ring-offset-0"
              )}
            />
          </div>

          {showPaginationControls && (
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {showingLabel ?? `${runs.length} runs`}
              </span>
              <button
                type="button"
                className={cn(
                  "inline-flex h-7 items-center gap-1 px-1 text-xs text-muted-foreground transition-colors",
                  hasPreviousPage
                    ? "hover:text-foreground"
                    : "cursor-not-allowed opacity-50"
                )}
                disabled={!hasPreviousPage || isLoading}
                onClick={goToPreviousPage}
                aria-label="Previous page"
              >
                <ChevronLeftIcon className="mr-1 size-3.5" />
                <span>Prev</span>
              </button>
              <button
                type="button"
                className={cn(
                  "inline-flex h-7 items-center gap-1 px-1 text-xs text-muted-foreground transition-colors",
                  hasNextPage
                    ? "hover:text-foreground"
                    : "cursor-not-allowed opacity-50"
                )}
                disabled={!hasNextPage || isLoading}
                onClick={goToNextPage}
                aria-label="Next page"
              >
                <span>Next</span>
                <ChevronRightIcon className="ml-1 size-3.5" />
              </button>
            </div>
          )}
        </header>

        <div className="flex flex-wrap items-center gap-2 py-2 pl-3 pr-4">
          {runs.length > 0 && (
            <button
              type="button"
              onClick={handleSelectAllVisible}
              className="flex h-7 w-7 shrink-0 items-center justify-center"
              aria-label={allVisibleSelected ? "Deselect all" : "Select all"}
              title={allVisibleSelected ? "Deselect all" : "Select all"}
            >
              <span
                className={cn(
                  "flex size-4 shrink-0 items-center justify-center rounded-sm border transition-colors",
                  allVisibleSelected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-muted-foreground/40 bg-transparent"
                )}
              >
                {allVisibleSelected && <Check className="size-3" aria-hidden />}
              </span>
            </button>
          )}

          <FilterMultiSelect
            placeholder="Relation"
            icon={SignalIcon}
            value={[relation]}
            options={RELATION_OPTIONS}
            onChange={(value) => {
              const nextRelation = value.at(-1) ?? "all"
              setRelation(nextRelation)
            }}
            mode="include"
            onModeChange={() => {}}
            allowExclude={false}
          />

          <FilterMultiSelect
            placeholder="Status"
            icon={SignalIcon}
            value={statusFilter}
            options={STATUS_FILTER_OPTIONS}
            onChange={setStatusFilter}
            mode={statusMode}
            onModeChange={setStatusMode}
          />

          <FilterMultiSelect
            placeholder="Trigger"
            icon={WebhookIcon}
            value={triggerFilter}
            options={TRIGGER_FILTER_OPTIONS}
            onChange={setTriggerFilter}
            mode="include"
            onModeChange={() => {}}
            allowExclude={false}
          />

          <FilterMultiSelect
            placeholder="User"
            icon={UserIcon}
            value={userFilter}
            options={userFilterOptions}
            onChange={(values) => {
              const nextUserId = values.at(-1)
              setUserFilter(nextUserId ? [nextUserId] : [])
            }}
            mode="include"
            onModeChange={() => {}}
            allowExclude={false}
            modeLabel="Triggered by"
          />

          <TimeFilterButton
            label="Start time"
            value={startTimeFilter}
            onChange={setStartTimeFilter}
          />

          <TimeFilterButton
            label="End time"
            value={closeTimeFilter}
            onChange={setCloseTimeFilter}
          />

          <DurationFilterButton
            minSeconds={durationMinSeconds}
            maxSeconds={durationMaxSeconds}
            onChange={({ minSeconds, maxSeconds }) => {
              setDurationMinSeconds(minSeconds)
              setDurationMaxSeconds(maxSeconds)
            }}
          />

          {selectedCount > 0 ? (
            <>
              <button
                type="button"
                className={cn(
                  "inline-flex h-6 items-center rounded-md border px-2 text-xs font-medium transition-colors",
                  "border-orange-500/40 bg-orange-500/10 text-orange-700 hover:bg-orange-500/15 dark:text-orange-300",
                  "disabled:cursor-not-allowed disabled:opacity-50"
                )}
                disabled={selectedRunningCount === 0 || isMutating}
                onClick={() => setBulkCancelConfirmOpen(true)}
              >
                Cancel selected ({selectedRunningCount})
              </button>

              <button
                type="button"
                className={cn(
                  "inline-flex h-6 items-center rounded-md border px-2 text-xs font-medium transition-colors",
                  "border-rose-500/40 bg-rose-500/10 text-rose-700 hover:bg-rose-500/15 dark:text-rose-300",
                  "disabled:cursor-not-allowed disabled:opacity-50"
                )}
                disabled={selectedRunningCount === 0 || isMutating}
                onClick={() => setBulkTerminateConfirmOpen(true)}
              >
                Terminate selected ({selectedRunningCount})
              </button>

              <button
                type="button"
                className={cn(
                  "inline-flex h-6 items-center rounded-md border px-2 text-xs font-medium transition-colors",
                  "border-blue-500/40 bg-blue-500/10 text-blue-700 hover:bg-blue-500/15 dark:text-blue-300",
                  "disabled:cursor-not-allowed disabled:opacity-50"
                )}
                disabled={isMutating}
                onClick={openBulkResetDialog}
              >
                Reset selected ({selectedCount})
              </button>
            </>
          ) : null}

          {hasFilters && (
            <button
              type="button"
              onClick={resetFilters}
              className="flex h-6 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              Reset
              <Cross2Icon className="size-3" />
            </button>
          )}

          <div className="ml-auto flex items-center">
            <Select
              value={`${limit}`}
              onValueChange={(value) => setLimit(Number(value))}
              disabled={isLoading}
            >
              <SelectTrigger className="h-6 w-auto gap-1.5 rounded-md px-2 text-xs font-medium">
                <span className="text-muted-foreground">Limit</span>
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                {LIMIT_OPTIONS.map((option) => (
                  <SelectItem key={option} value={`${option}`}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="flex h-full items-center justify-center">
            <CenteredSpinner />
          </div>
        ) : filteredGroups.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            No runs found.
          </div>
        ) : (
          <>
            <AccordionPrimitive.Root
              type="multiple"
              defaultValue={filteredGroups.map((group) => group.key)}
              className="w-full"
            >
              {filteredGroups.map((group, groupIndex) => {
                const runsInGroup = groupedRuns[group.key] ?? []
                const groupExecutionIds = runsInGroup.map((run) => run.id)
                const selectedInGroupCount = runsInGroup.filter((run) =>
                  selectedExecutionIds.has(run.id)
                ).length
                const allGroupSelected =
                  runsInGroup.length > 0 &&
                  selectedInGroupCount === runsInGroup.length
                const someGroupSelected =
                  selectedInGroupCount > 0 && !allGroupSelected
                return (
                  <AccordionPrimitive.Item
                    value={group.key}
                    key={group.key}
                    className="group/accordion border-b border-border/50"
                    data-status={group.key.toLowerCase()}
                  >
                    <AccordionPrimitive.Header className="flex">
                      <AccordionPrimitive.Trigger
                        className={cn(
                          "flex w-full items-center gap-1 border-l-2 border-l-transparent py-1.5 pl-[10px] pr-3 text-left transition-colors",
                          "hover:bg-muted/50",
                          "[&[data-state=open]_.chevron]:rotate-90",
                          "data-[state=open]:border-l-current",
                          group.panelClassName
                        )}
                      >
                        <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                          <ChevronRightIcon className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                        </div>
                        <div className="flex items-center gap-1.5">
                          {isRunStatus(group.key) ? (
                            <WorkflowExecutionStatusIcon
                              status={group.key}
                              className={cn("size-4", group.iconClassName)}
                            />
                          ) : (
                            <CircleHelpIcon
                              className={cn("size-4", group.iconClassName)}
                            />
                          )}
                          <span className="text-xs font-medium">
                            {group.label}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {runsInGroup.length}
                          </span>
                          <span
                            role="checkbox"
                            tabIndex={0}
                            aria-checked={
                              someGroupSelected ? "mixed" : allGroupSelected
                            }
                            aria-label={
                              allGroupSelected
                                ? `Deselect all ${group.label.toLowerCase()} runs`
                                : `Select all ${group.label.toLowerCase()} runs`
                            }
                            className="ml-0.5 flex size-4 shrink-0 items-center justify-center rounded-sm border transition-colors"
                            onPointerDown={(event) => {
                              event.preventDefault()
                              event.stopPropagation()
                            }}
                            onClick={(event) => {
                              event.preventDefault()
                              event.stopPropagation()
                              handleToggleGroupSelection({
                                groupIndex,
                                executionIds: groupExecutionIds,
                                checked: !allGroupSelected,
                                shiftKey: event.shiftKey,
                                additive: event.ctrlKey || event.metaKey,
                              })
                            }}
                            onKeyDown={(event) => {
                              if (
                                event.key !== "Enter" &&
                                event.key !== " " &&
                                event.key !== "Spacebar"
                              ) {
                                return
                              }
                              event.preventDefault()
                              event.stopPropagation()
                              handleToggleGroupSelection({
                                groupIndex,
                                executionIds: groupExecutionIds,
                                checked: !allGroupSelected,
                                shiftKey: event.shiftKey,
                                additive: event.ctrlKey || event.metaKey,
                              })
                            }}
                          >
                            <span
                              className={cn(
                                "flex size-4 shrink-0 items-center justify-center rounded-sm border transition-colors",
                                allGroupSelected || someGroupSelected
                                  ? "border-primary bg-primary text-primary-foreground"
                                  : "border-muted-foreground/40 bg-transparent"
                              )}
                            >
                              {allGroupSelected ? (
                                <Check className="size-3" aria-hidden />
                              ) : someGroupSelected ? (
                                <Minus className="size-3" aria-hidden />
                              ) : null}
                            </span>
                          </span>
                        </div>
                      </AccordionPrimitive.Trigger>
                    </AccordionPrimitive.Header>
                    <AccordionPrimitive.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
                      <div className="ml-[18px]">
                        {runsInGroup.map((run) => (
                          <WorkflowRunItem
                            key={run.id}
                            run={run}
                            checked={selectedExecutionIds.has(run.id)}
                            onCheckedChange={(checked) =>
                              handleToggleRunSelection(run.id, checked)
                            }
                            onCancel={handleCancelRun}
                            onTerminate={handleTerminateRun}
                            onReset={openSingleResetDialog}
                          />
                        ))}
                      </div>
                    </AccordionPrimitive.Content>
                  </AccordionPrimitive.Item>
                )
              })}
            </AccordionPrimitive.Root>
          </>
        )}
      </div>

      <ResetWorkflowRunDialog
        open={resetDialogOpen}
        onOpenChange={setResetDialogOpen}
        executionCount={resetTargetExecutionIds.length}
        resetPoints={resetPoints ?? []}
        resetPointsLoading={resetPointsLoading}
        isSubmitting={isResetting || isBulkResetting}
        onSubmit={handleResetSubmit}
      />

      <AlertDialog
        open={bulkCancelConfirmOpen}
        onOpenChange={setBulkCancelConfirmOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel selected runs</AlertDialogTitle>
            <AlertDialogDescription>
              This will request cancellation for {selectedRunningCount} running
              run(s). Non-running runs are ignored.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isBulkCanceling}>
              Keep runs
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={isBulkCanceling || selectedRunningCount === 0}
              onClick={(event) => {
                event.preventDefault()
                void handleBulkCancelSelected()
              }}
            >
              {isBulkCanceling ? "Canceling..." : "Cancel runs"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={bulkTerminateConfirmOpen}
        onOpenChange={setBulkTerminateConfirmOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Terminate selected runs</AlertDialogTitle>
            <AlertDialogDescription>
              This will immediately terminate {selectedRunningCount} running
              run(s). This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isBulkTerminating}>
              Keep runs
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={isBulkTerminating || selectedRunningCount === 0}
              onClick={(event) => {
                event.preventDefault()
                void handleBulkTerminateSelected()
              }}
            >
              {isBulkTerminating ? "Terminating..." : "Terminate runs"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
