import { z } from "zod"

import { stringToJSONSchema } from "@/types/validators"

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
  icon_url: z.string().url().nullable(),
})
export type WorkflowMetadata = z.infer<typeof workflowMetadataSchema>
const strAsDate = z.string().transform((x) => new Date(`${x}Z`))

const runStatusSchema = z.enum([
  "pending",
  "success",
  "failure",
  "canceled",
  "running",
])
export type RunStatus = z.infer<typeof runStatusSchema>

export const actionRunSchema = z.object({
  id: z.string(),
  created_at: strAsDate,
  updated_at: strAsDate,
  action_id: z.string(),
  status: runStatusSchema,
  error_msg: z.string().nullable(),
  result: stringToJSONSchema.nullable(),
})
export type ActionRun = z.infer<typeof actionRunSchema>

export const workflowRunSchema = z.object({
  id: z.string(),
  created_at: strAsDate,
  updated_at: strAsDate,
  workflow_id: z.string(),
  status: runStatusSchema,
  action_runs: z.array(actionRunSchema),
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
  created_at: z.string(),
  updated_at: z.string(),
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

export const secretSchema = z.object({
  id: z.string().min(1).optional(),
  name: z.string().min(1, "Please enter a secret name."),
  value: z.string().min(1, "Please enter the secret value."),
})

export type Secret = z.infer<typeof secretSchema>
