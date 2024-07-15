import { z } from "zod"

// Ensure all values are positive
// Finally validate that at least one component is present
export const durationSchema = z
  .object({
    years: z.number().int().positive().optional(),
    months: z.number().int().positive().optional(),
    weeks: z.number().int().positive().optional(),
    days: z.number().int().positive().optional(),
    hours: z.number().int().positive().optional(),
    minutes: z.number().int().positive().optional(),
    seconds: z.number().int().positive().optional(),
  })
  .transform((data) => {
    if (
      !data.years &&
      !data.months &&
      !data.weeks &&
      !data.days &&
      !data.hours &&
      !data.minutes &&
      !data.seconds
    ) {
      throw new Error("Duration must have at least one component")
    }
    return data
  })

export type Duration = z.infer<typeof durationSchema>

export function durationToISOString(duration: Duration): string {
  durationSchema.parse(duration)
  // Do not allow the duration to be empty
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
