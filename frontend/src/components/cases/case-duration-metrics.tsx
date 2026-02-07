"use client"

import { format, intervalToDuration, isValid as isValidDate } from "date-fns"
import { FlagTriangleRight, Hourglass } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { CaseDurationDefinitionRead, CaseDurationRead } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { parseISODuration } from "@/lib/time"

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

export function CaseDurationMetrics({
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
