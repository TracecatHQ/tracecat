import { z } from "zod"


export const caseSchema = z.object({
  id: z.number().int(),
  title: z.string(),
  payload:z.record(z.string()).transform((val) => JSON.stringify(val)),
  malice: z.enum(["malicious", "benign"]),
  action: z.array(z.string()).transform((val) => val[0]),
  context: z.array(z.string()),
  suppression: z.record(z.boolean()).transform((val) => JSON.stringify(val)),
  status: z.string(),
  priority: z.enum(["low", "medium", "high", "critical"]),
})

export type Task = z.infer<typeof caseSchema>
