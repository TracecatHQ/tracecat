import { z } from "zod"

export const eventSchema = z.object({
  published_at: z.string().datetime(),
  workflow_run_id: z.string(),
  action_title: z.string(),
  trail: z.record(z.string()).transform((val) => JSON.stringify(val)),
})

export type Event = z.infer<typeof eventSchema>
