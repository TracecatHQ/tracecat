import { z } from "zod"

import {
  keyValueSchema,
  stringToJSONSchema,
  tagSchema,
} from "@/types/validators"

/**
 * Core action types.
 */
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

/**
 * Integration types are prefixed with the platform name.
 * This is to ensure that integrations from different platforms do not clash.
 *
 * Format: integrations.<platform>.<optional_namespaces>.<function>
 *
 * Not sure how to generate this dynamically - cuyrrently needs to be manually updated.
 */
const integrationTypes = [
  // Example integrations
  "integrations.example.add",
  "integrations.example.secretive_add",
  "integrations.example.subtract",
  "integrations.example.complex_example",
  "integrations.example.join",
  // Material Security
  "integrations.sublime_security.test",
  // Datadog
  "integrations.datadog.test",
] as const
export type IntegrationType = (typeof integrationTypes)[number]

/**
 * All platforms that are supported by the system.
 */
const integrationPlatforms = ["example", "sublime_security", "datadog"] as const
export type IntegrationPlatform = (typeof integrationPlatforms)[number]

export type NodeType = ActionType | IntegrationType

const actionStatusSchema = z.enum(["online", "offline"])
export type ActionStatus = z.infer<typeof actionStatusSchema>

export const actionSchema = z.object({
  id: z.string(),
  type: z.enum(actionTypes).or(z.enum(integrationTypes)),
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
const strAsDate = z.string().transform((x) => new Date(x))

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
  workflow_run_id: z.string(),
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
  action_runs: z.array(actionRunSchema).nullish().default([]),
})

export type WorkflowRun = z.infer<typeof workflowRunSchema>

export const suppressionSchema = z.object({
  condition: z.string().min(1, "Please enter a suppression condition."),
  result: z.string().min(1, "Please enter a template expression or boolean"),
})

export const caseActionTypes = [
  "ignore",
  "quarantine",
  "informational",
  "sinkhole",
  "active_compromise",
] as const
export type CaseActionType = (typeof caseActionTypes)[number]

export const caseSchema = z.object({
  // SQLModel metadata
  id: z.string(),
  owner_id: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  // Case related data
  workflow_id: z.string(),
  title: z.string(),
  payload: z.record(z.string()),
  malice: z.enum(["malicious", "benign"]),
  status: z.enum(["open", "closed", "in_progress", "reported", "escalated"]),
  priority: z.enum(["low", "medium", "high", "critical"]),
  // This needs to be a union type because it's serialized as a string
  context: z.record(z.string()).nullable().or(z.string()),
  action: z.enum(caseActionTypes),
  suppression: z.array(suppressionSchema).nullish().default([]),
  tags: z.array(tagSchema).nullish().default([]),
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

export const secretTypes = ["custom", "token", "oauth2"] as const
export type SecretType = (typeof secretTypes)[number]

const snakeCaseRegex = /^[a-z]+(_[a-z]+)*$/
export const secretSchema = z.object({
  id: z.string().min(1).optional(),
  type: z.enum(secretTypes),
  name: z
    .string()
    .min(1, "Please enter a secret name.")
    .regex(snakeCaseRegex, "Secret name must be snake case."),
  description: z.string().max(255).nullish(),
  // Can take different types of secrets
  keys: z.array(keyValueSchema),
})

export type Secret = z.infer<typeof secretSchema>

export const integrationSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  docstring: z.string(),
  parameters: stringToJSONSchema,
  platform: z.enum(integrationPlatforms),
  icon_url: z.string().url().nullable(),
})

export type Integration = z.infer<typeof integrationSchema>
