import { z } from "zod"

const actionTypes = [
  "webhook",
  "http_request",
  "data_transform",
  "condition.compare",
  "condition.regex",
  "condition.membership",
  "open_case",
  "receive_email",
  "send_email",
  "llm.extract",
  "llm.label",
  "llm.translate",
  "llm.choice",
  "llm.summarize",
] as const
export type ActionType = (typeof actionTypes)[number]

const actionStatusSchema = z.enum(["online", "offline"])
export type ActionStatus = z.infer<typeof actionStatusSchema>

export const actionSchema = z.object({
  id: z.string(),
  type: z.enum(actionTypes),
  title: z.string(),
  description: z.string(),
  status: actionStatusSchema,
  inputs: z.record(z.any()).nullable(),
})

export type Action = z.infer<typeof actionSchema>

export const actionMetadataSchema = z.object({
  id: z.string(),
  workflow_id: z.string(),
  title: z.string(),
  description: z.string(),
})
export type ActionMetadata = z.infer<typeof actionMetadataSchema>

const workflowStatusSchema = z.enum(["online", "offline"])
export type WorkflowStatus = z.infer<typeof workflowStatusSchema>

export const workflowSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: workflowStatusSchema,
  actions: z.record(actionSchema),
  object: z.record(z.any()).nullable(),
})

export type Workflow = z.infer<typeof workflowSchema>

export const workflowMetadataSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  status: workflowStatusSchema,
})
export type WorkflowMetadata = z.infer<typeof workflowMetadataSchema>

const workflowRunStatusSchema = z.enum([
  "pending",
  "success",
  "failure",
  "canceled",
  "running",
])
export type WorkflowRunStatus = z.infer<typeof workflowRunStatusSchema>

export const workflowRunSchema = z.object({
  id: z.string(),
  workflow_id: z.string(),
  status: workflowRunStatusSchema,
  created_at: z.string(),
  updated_at: z.string(),
})
export type WorkflowRun = z.infer<typeof workflowRunSchema>

export const caseSchema = z.object({
  id: z.string(),
  owner_id: z.string(),
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

export const triggerWorkflowSchema = z.object({
  workflow_id: z.string(),
  action_id: z.string(),
  payload: z.record(z.string()),
})

export type TriggerWorkflow = z.infer<typeof triggerWorkflowSchema>
