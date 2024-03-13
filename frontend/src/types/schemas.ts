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
  actions: z.record(actionResponseSchema),
  object: z.record(z.any()).nullable(),
})

export type WorkflowResponse = z.infer<typeof workflowResponseSchema>

export const caseSchema = z.object({
  id: z.string(),
  workflow_id: z.string(),
  title: z.string(),
  payload: z.record(z.string()),
  malice: z.enum(["malicious", "benign"]),
  status: z.enum(["open", "closed", "in_progress", "reported", "escalated"]),
  priority: z.enum(["low", "medium", "high", "critical"]),
  context: z.record(z.string()).nullable().or(z.string()),
  action: z.string().nullable(),
  suppression: z.record(z.boolean()).nullable(),
})

export type Case = z.infer<typeof caseSchema>

export const caseCompletionUpdateSchema = z.object({
  id: z.string(),
  response: z.object({
    context: z.record(z.string()),
    action: z.string(),
  }),
})
export type CaseCompletionUpdate = z.infer<typeof caseCompletionUpdateSchema>
