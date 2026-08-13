"use client"

import { format, isValid as isValidDate } from "date-fns"
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
  durationBetween,
  formatDurationCompact,
  formatDurationLong,
  parseISODurationSafe,
} from "@/lib/time"
import { cn } from "@/lib/utils"

/**
 * Age past which a pill stops rendering seconds.
 *
 * Tied to `formatDurationCompact`'s two-unit window: at an hour the window is
 * "Xh Ym", so the seconds field leaves the screen and a per-second tick stops
 * changing anything. Pinned by a test in `tests/time.test.ts`.
 */
const SLOW_TICK_THRESHOLD_MS = 60 * 60 * 1000

const FAST_TICK_MS = 1000
const SLOW_TICK_MS = 30_000

function parseCaseTimestamp(value?: string | null): Date | null {
  if (!value) return null
  const date = new Date(value)
  return isValidDate(date) ? date : null
}

/** Shared so the local and UTC timestamps always read the same shape. */
const DATE_TIME_PATTERN = "MMM d yyyy '·' p"

function formatLocalDateTime(date: Date): string {
  return format(date, DATE_TIME_PATTERN)
}

const UTC_DATE_TIME_PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
})

/**
 * Same shape as {@link formatLocalDateTime}, rendered in UTC.
 *
 * Assembled from `formatToParts` rather than shifting the instant by the local
 * UTC offset, because that shift lands on the wrong side of a DST transition
 * and silently reports the hour before or after the real one.
 */
function formatUtcDateTime(date: Date): string {
  const parts = new Map(
    UTC_DATE_TIME_PARTS.formatToParts(date).map((part) => [
      part.type,
      part.value,
    ])
  )
  const month = parts.get("month") ?? ""
  const day = parts.get("day") ?? ""
  const year = parts.get("year") ?? ""
  const hour = parts.get("hour") ?? ""
  const minute = parts.get("minute") ?? ""
  const dayPeriod = parts.get("dayPeriod") ?? ""
  return `${month} ${day} ${year} · ${hour}:${minute} ${dayPeriod} UTC`
}

interface CaseDurationMetric {
  id: string
  name: string
  description?: string | null
  startedAt: Date
  endedAt: Date | null
  /** Truncated pill value, e.g. "1d 5h". */
  displayValue: string
  /** Unabbreviated hover-card value, e.g. "1 day, 5 hours, 15 minutes". */
  preciseValue: string
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

        // Parse once, render twice: the pill gets the truncated form and the
        // hover card the full one. A completed duration prefers the value the
        // backend computed, falling back to the interval it spans.
        const parts = endedAt
          ? (parseISODurationSafe(duration.duration) ??
            durationBetween(startedAt, endedAt))
          : durationBetween(startedAt, now)

        return {
          id: duration.id,
          name,
          description,
          startedAt,
          endedAt,
          displayValue: formatDurationCompact(parts),
          preciseValue: formatDurationLong(parts),
          state: endedAt ? "done" : "ongoing",
        }
      })
      .filter((item): item is CaseDurationMetric => item !== null)
  }, [definitionById, durations, now])

  const hasOngoing = metrics.some((metric) => metric.state === "ongoing")
  // Only tick every second while some pill is actually showing seconds.
  const showsSeconds = metrics.some(
    (metric) =>
      metric.state === "ongoing" &&
      now.getTime() - metric.startedAt.getTime() < SLOW_TICK_THRESHOLD_MS
  )

  useEffect(() => {
    if (!hasOngoing) {
      return
    }
    const interval = window.setInterval(
      () => setNow(new Date()),
      showsSeconds ? FAST_TICK_MS : SLOW_TICK_MS
    )
    return () => window.clearInterval(interval)
  }, [hasOngoing, showsSeconds])

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
      className={cn(
        "flex items-center gap-2",
        isInline ? "flex-nowrap" : "flex-wrap"
      )}
    >
      {metrics.map((metric) => {
        const IconComponent =
          metric.state === "ongoing" ? Hourglass : FlagTriangleRight

        return (
          <HoverCard key={metric.id} openDelay={100} closeDelay={100}>
            <HoverCardTrigger asChild>
              <Badge
                variant="outline"
                className={cn(
                  "gap-1.5 whitespace-nowrap px-2 py-1 text-xs font-medium bg-background text-foreground",
                  // Inline pills size to their content and never compress, so a
                  // short name like "TTR" takes only the width it needs. Once
                  // the row outgrows its slot it scrolls, rather than squeezing
                  // every pill until the values are clipped out of view.
                  isInline && "shrink-0"
                )}
              >
                <span className="inline-flex text-muted-foreground">
                  <IconComponent aria-hidden="true" className="h-3.5 w-3.5" />
                </span>
                {/* Long names cut off at 9rem with an ellipsis. */}
                <span className="min-w-0 max-w-[9rem] truncate">
                  {metric.name}
                </span>
                <span className="shrink-0 whitespace-nowrap font-mono tabular-nums text-muted-foreground">
                  {metric.displayValue}
                </span>
              </Badge>
            </HoverCardTrigger>
            <HoverCardContent className="w-80">
              <div className="flex flex-col gap-3 text-xs">
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    {metric.name}
                  </p>
                  {metric.description ? (
                    <p className="mt-1 text-muted-foreground">
                      {metric.description}
                    </p>
                  ) : null}
                </div>
                <div className="space-y-3">
                  <div>
                    <p className="font-medium text-muted-foreground">
                      {metric.state === "ongoing" ? "Elapsed" : "Duration"}
                    </p>
                    <p className="mt-1">{metric.preciseValue}</p>
                  </div>
                  <div>
                    <p className="font-medium text-muted-foreground">
                      Started at
                    </p>
                    <p className="mt-1">
                      {formatLocalDateTime(metric.startedAt)}
                    </p>
                    <p className="text-muted-foreground">
                      {formatUtcDateTime(metric.startedAt)}
                    </p>
                  </div>
                  <div>
                    <p className="font-medium text-muted-foreground">
                      Ended at
                    </p>
                    {metric.endedAt ? (
                      <>
                        <p className="mt-1">
                          {formatLocalDateTime(metric.endedAt)}
                        </p>
                        <p className="text-muted-foreground">
                          {formatUtcDateTime(metric.endedAt)}
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

  if (isInline) {
    return metricsList
  }

  return <div className="py-1.5 first:pt-0 last:pb-0">{metricsList}</div>
}
