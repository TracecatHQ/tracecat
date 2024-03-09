import { z } from "zod"

export const actionResponseSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: z.string(),
  inputs: z.record(z.any()).optional(),
})

export type ActionResponse = z.infer<typeof actionResponseSchema>

export const workflowMetadataSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: z.enum(["online", "offline"]),
})
export type WorkflowMetadata = z.infer<typeof workflowMetadataSchema>
