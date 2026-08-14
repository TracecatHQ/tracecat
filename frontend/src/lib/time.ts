import { formatDistanceToNowStrict, intervalToDuration } from "date-fns"
import { z } from "zod"

/**
 * Format a timestamp as a relative-to-now string, e.g. "3 minutes ago".
 *
 * Returns `null` when the input is missing or not a valid date, so callers
 * can render conditionally without wrapping in try/catch.
 */
export function formatRelative(
  value: string | Date | null | undefined
): string | null {
  if (!value) return null
  try {
    const date = value instanceof Date ? value : new Date(value)
    if (Number.isNaN(date.getTime())) return null
    return formatDistanceToNowStrict(date, { addSuffix: true })
  } catch {
    return null
  }
}

// Ensure all values are positive
// Finally validate that at least one component is present
export const durationSchema = z
  .object({
    years: z.number().int().default(0),
    months: z.number().int().default(0),
    weeks: z.number().int().default(0),
    days: z.number().int().default(0),
    hours: z.number().int().default(0),
    minutes: z.number().int().default(0),
    seconds: z.number().int().default(0),
  })
  .transform((data) => {
    // Check that there's at least one component in the duration
    if (
      data.years === 0 &&
      data.months === 0 &&
      data.weeks === 0 &&
      data.days === 0 &&
      data.hours === 0 &&
      data.minutes === 0 &&
      data.seconds === 0
    ) {
      throw new Error("Please provide at least one component in the duration.")
    }
    return data
  })

export type Duration = z.infer<typeof durationSchema>

export function durationToISOString(duration: Duration): string {
  // Do not need to parse durationSchema since the default values are already set
  let result = "P"

  if (duration.years) result += `${duration.years}Y`
  if (duration.months) result += `${duration.months}M`
  if (duration.weeks) result += `${duration.weeks}W`
  if (duration.days) result += `${duration.days}D`

  if (duration.hours || duration.minutes || duration.seconds) {
    result += "T"
    if (duration.hours) result += `${duration.hours}H`
    if (duration.minutes) result += `${duration.minutes}M`
    if (duration.seconds) result += `${duration.seconds}S`
  }

  if (result === "P") {
    result += "0D" // ISO 8601 requires at least one component in the duration
  }

  return result
}

export function parseISODuration(duration: string): Duration {
  const regex =
    /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/
  const matches = duration.match(regex)

  if (!matches) {
    throw new Error("Invalid ISO 8601 duration format")
  }

  const [
    ,
    // Full match (ignored)
    years,
    months,
    weeks,
    days,
    hours,
    minutes,
    seconds,
  ] = matches

  const parsed = {
    years: years ? parseInt(years, 10) : 0,
    months: months ? parseInt(months, 10) : 0,
    weeks: weeks ? parseInt(weeks, 10) : 0,
    days: days ? parseInt(days, 10) : 0,
    hours: hours ? parseInt(hours, 10) : 0,
    minutes: minutes ? parseInt(minutes, 10) : 0,
    seconds: seconds ? Math.round(parseFloat(seconds)) : 0,
  }

  if (parsed.seconds >= 60) {
    parsed.minutes += Math.floor(parsed.seconds / 60)
    parsed.seconds %= 60
  }

  if (parsed.minutes >= 60) {
    parsed.hours += Math.floor(parsed.minutes / 60)
    parsed.minutes %= 60
  }

  if (parsed.hours >= 24) {
    parsed.days += Math.floor(parsed.hours / 24)
    parsed.hours %= 24
  }

  return parsed
}

/**
 * Duration units ordered from most to least significant.
 *
 * Weeks are deliberately absent: they are folded into days before rendering, so
 * leaving the slot in place would let an always-zero unit consume a position in
 * the contiguous window and swallow the unit below it (e.g. "2mo 20d" → "2mo").
 */
const DURATION_UNIT_ORDER = [
  "years",
  "months",
  "days",
  "hours",
  "minutes",
  "seconds",
] as const satisfies readonly (keyof Duration)[]

/**
 * Suffixes used by the compact duration renderer.
 *
 * Keyed off the render order rather than `keyof Duration`, so the absence of
 * `weeks` above is enforced by the compiler instead of by the comment.
 */
const DURATION_SUFFIXES: Record<(typeof DURATION_UNIT_ORDER)[number], string> =
  {
    years: "y",
    months: "mo",
    days: "d",
    hours: "h",
    minutes: "m",
    seconds: "s",
  }

/** Options shared by the compact duration formatters. */
export interface FormatDurationCompactOptions {
  /**
   * Width of the contiguous unit window, starting at the largest non-zero unit.
   * Valid range is 1 to 6 (the number of rendered units); values outside it are
   * clamped. Defaults to 2.
   */
  maxUnits?: number
}

/**
 * Render at most `maxUnits` (default 2) of the most significant units, e.g.
 * "1d 5h", "15m 17s", "25s".
 *
 * Weeks are folded into days first. The window starts at the largest non-zero
 * unit and spans `maxUnits` *contiguous* units, skipping any that are zero, so
 * `{ days: 1, minutes: 59 }` renders as "1d" rather than "1d 59m" — a window of
 * positions, not a count of rendered parts. This keeps a live ticker's
 * transitions smooth and bounds the rendered width.
 *
 * Values are truncated, never rounded. An empty or all-zero duration renders as
 * "0s".
 */
export function formatDurationCompact(
  duration: Partial<Duration>,
  options?: FormatDurationCompactOptions
): string {
  const requested = options?.maxUnits ?? 2
  const maxUnits = Number.isFinite(requested)
    ? Math.min(Math.max(1, Math.floor(requested)), DURATION_UNIT_ORDER.length)
    : 2

  const normalized: Duration = {
    years: duration.years ?? 0,
    months: duration.months ?? 0,
    weeks: 0,
    days: (duration.days ?? 0) + (duration.weeks ?? 0) * 7,
    hours: duration.hours ?? 0,
    minutes: duration.minutes ?? 0,
    seconds: duration.seconds ?? 0,
  }

  const startIndex = DURATION_UNIT_ORDER.findIndex(
    (unit) => normalized[unit] !== 0
  )
  if (startIndex === -1) {
    return "0s"
  }

  const parts: string[] = []
  const endIndex = Math.min(startIndex + maxUnits, DURATION_UNIT_ORDER.length)
  for (let index = startIndex; index < endIndex; index++) {
    const unit = DURATION_UNIT_ORDER[index]
    const value = normalized[unit]
    if (value === 0) continue
    parts.push(`${value}${DURATION_SUFFIXES[unit]}`)
  }

  return parts.length > 0 ? parts.join(" ") : "0s"
}

/**
 * Non-throwing {@link parseISODuration}; `null` when absent or unparseable.
 *
 * Prefer this at render sites, where a malformed value should degrade rather
 * than take the tree down.
 */
export function parseISODurationSafe(iso?: string | null): Duration | null {
  if (!iso) return null
  try {
    return parseISODuration(iso)
  } catch {
    return null
  }
}

/**
 * Components of the interval between two instants, empty when it is inverted or
 * zero-length. Calendar-aware, so months and years reflect real month lengths.
 */
export function durationBetween(start: Date, end: Date): Partial<Duration> {
  if (start >= end) return {}
  return intervalToDuration({ start, end })
}

/**
 * Compact form of a backend ISO-8601 duration string; `null` when unparseable
 * or when no duration was supplied.
 */
export function formatISODurationCompact(
  iso?: string | null,
  options?: FormatDurationCompactOptions
): string | null {
  const parsed = parseISODurationSafe(iso)
  if (!parsed) return null
  return formatDurationCompact(parsed, options)
}

/**
 * Compact elapsed time between two instants, e.g. "1d 5h". Returns "0s" when
 * the interval is empty or inverted.
 *
 * Truncating (never rounding) matters most here: a live count-up must never
 * appear to jump backwards between ticks.
 */
export function formatIntervalCompact(
  start: Date,
  end: Date,
  options?: FormatDurationCompactOptions
): string {
  return formatDurationCompact(durationBetween(start, end), options)
}

/**
 * Units rendered in the long form, most to least significant.
 *
 * Unlike {@link DURATION_UNIT_ORDER} this keeps `weeks`, because the long form
 * also renders user-configured schedule intervals where "2 weeks" is what the
 * user typed and "14 days" would be a regression.
 */
const DURATION_LONG_UNITS = [
  "years",
  "months",
  "weeks",
  "days",
  "hours",
  "minutes",
  "seconds",
] as const satisfies readonly (keyof Duration)[]

/** Singular nouns for the long duration renderer; pluralised on render. */
const DURATION_LONG_NOUNS: Record<
  (typeof DURATION_LONG_UNITS)[number],
  string
> = {
  years: "year",
  months: "month",
  weeks: "week",
  days: "day",
  hours: "hour",
  minutes: "minute",
  seconds: "second",
}

/**
 * Render every non-zero unit in full, e.g. "1 day, 5 hours, 15 minutes".
 *
 * This is the unabbreviated counterpart to {@link formatDurationCompact}, for
 * surfaces with no width pressure such as tooltips and hover cards. An empty or
 * all-zero duration renders as "0 seconds".
 */
export function formatDurationLong(duration: Partial<Duration>): string {
  const parts: string[] = []
  for (const unit of DURATION_LONG_UNITS) {
    const value = duration[unit] ?? 0
    if (value === 0) continue
    const noun = DURATION_LONG_NOUNS[unit]
    parts.push(`${value} ${noun}${value > 1 ? "s" : ""}`)
  }
  return parts.length > 0 ? parts.join(", ") : "0 seconds"
}

/**
 * Long form of a backend ISO-8601 duration string.
 *
 * Throws on malformed input; use {@link parseISODurationSafe} with
 * {@link formatDurationLong} where the value is untrusted.
 */
export function durationToHumanReadable(duration: string): string {
  return formatDurationLong(parseISODuration(duration))
}
