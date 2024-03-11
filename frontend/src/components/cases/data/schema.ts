import { z } from "zod"


export const caseSchema = z.object({
  id: z.number().int(),
  title: z.string(),
  payload: z.record(z.string()).transform((val) => JSON.stringify(val)),
  malice: z.enum(["malicious", "benign"]),
  status: z.enum(["open", "closed", "in_progress", "reported", "escalated"]),
  priority: z.enum(["low", "medium", "high", "critical"]),
  context: z.record(z.string()),
  action: z.string(),
  suppression: z.record(z.boolean()).transform((val) => JSON.stringify(val)),
})

export type Case = z.infer<typeof caseSchema>
