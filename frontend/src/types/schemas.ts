import { z } from "zod"

export const actionResponseSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: z.enum(["online", "offline"]),
  inputs: z.record(z.any()).nullable(),
})

export type ActionResponse = z.infer<typeof actionResponseSchema>

export const actionMetadataSchema = z.object({
  id: z.string(),
  workflow_id: z.string(),
  title: z.string(),
  description: z.string(),
})
export type ActionMetadata = z.infer<typeof actionMetadataSchema>

export const workflowMetadataSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: z.enum(["online", "offline"]),
})
export type WorkflowMetadata = z.infer<typeof workflowMetadataSchema>

export const workflowResponseSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: z.string(),
  actions: z.record(z.array(actionResponseSchema)),
  object: z.record(z.any()).nullable(),
})

export type WorkflowResponse = z.infer<typeof workflowResponseSchema>
