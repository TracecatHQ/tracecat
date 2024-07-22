import { z } from "zod"

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
    /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/
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

  return {
    years: years ? parseInt(years, 10) : 0,
    months: months ? parseInt(months, 10) : 0,
    weeks: weeks ? parseInt(weeks, 10) : 0,
    days: days ? parseInt(days, 10) : 0,
    hours: hours ? parseInt(hours, 10) : 0,
    minutes: minutes ? parseInt(minutes, 10) : 0,
    seconds: seconds ? parseInt(seconds, 10) : 0,
  }
}

export function durationToHumanReadable(duration: string): string {
  const parsedDuration = parseISODuration(duration)
  const parts: string[] = []

  if (parsedDuration.years)
    parts.push(
      `${parsedDuration.years} year${parsedDuration.years > 1 ? "s" : ""}`
    )
  if (parsedDuration.months)
    parts.push(
      `${parsedDuration.months} month${parsedDuration.months > 1 ? "s" : ""}`
    )
  if (parsedDuration.weeks)
    parts.push(
      `${parsedDuration.weeks} week${parsedDuration.weeks > 1 ? "s" : ""}`
    )
  if (parsedDuration.days)
    parts.push(
      `${parsedDuration.days} day${parsedDuration.days > 1 ? "s" : ""}`
    )
  if (parsedDuration.hours)
    parts.push(
      `${parsedDuration.hours} hour${parsedDuration.hours > 1 ? "s" : ""}`
    )
  if (parsedDuration.minutes)
    parts.push(
      `${parsedDuration.minutes} minute${parsedDuration.minutes > 1 ? "s" : ""}`
    )
  if (parsedDuration.seconds)
    parts.push(
      `${parsedDuration.seconds} second${parsedDuration.seconds > 1 ? "s" : ""}`
    )

  return parts.length > 0 ? parts.join(", ") : "0 seconds"
}
