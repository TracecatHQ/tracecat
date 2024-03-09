import { z } from "zod"

export const actionResponseSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: z.string(),
  inputs: z.record(z.any()).optional(),
})

export type ActionResponse = z.infer<typeof actionResponseSchema>
