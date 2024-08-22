import { z } from "zod"

import { keyValueSchema } from "@/types/validators"

export const strAsDate = z.string().transform((x) => new Date(x))

// Common resource Schema
export const resourceSchema = z.object({
  id: z.string(),
  owner_id: z.string(),
  created_at: strAsDate,
  updated_at: strAsDate,
})

export type Resource = z.infer<typeof resourceSchema>

/**
 * A trigger contains one or more webhooks and or schedules that can trigger a workflow.
 */
export const webhookSchema = z
  .object({
    status: z.enum(["online", "offline"]),
    method: z.enum(["GET", "POST"]),
    filters: z.record(z.any()),
    // Computed fields
    url: z.string().url(),
    secret: z.string(),
  })
  .and(resourceSchema)
export type Webhook = z.infer<typeof webhookSchema>

/**
 * A trigger contains one or more webhooks and or schedules that can trigger a workflow.
 */
export const scheduleSchema = z
  .object({
    status: z.enum(["online", "offline"]),
    inputs: z.record(z.any()),
    cron: z.string().nullish(),
    every: z.string(),
    offset: z.string().nullable(),
    start_at: strAsDate.nullable(),
    end_at: strAsDate.nullable(),
    workflow_id: z.string(),
  })
  .and(resourceSchema)
export type Schedule = z.infer<typeof scheduleSchema>
/** Workflow Schemas */

export const caseActionTypes = [
  "ignore",
  "quarantine",
  "informational",
  "sinkhole",
  "active_compromise",
] as const
export type CaseActionType = (typeof caseActionTypes)[number]

export const caseMaliceTypes = ["malicious", "benign"] as const
export type CaseMaliceType = (typeof caseMaliceTypes)[number]

export const caseStatusTypes = [
  "open",
  "closed",
  "in_progress",
  "reported",
  "escalated",
] as const
export type CaseStatusType = (typeof caseStatusTypes)[number]

export const casePriorityTypes = ["low", "medium", "high", "critical"] as const
export type CasePriorityType = (typeof casePriorityTypes)[number]

export const secretTypes = ["custom", "token", "oauth2"] as const
export type SecretType = (typeof secretTypes)[number]

export const createSecretSchema = z.object({
  id: z.string().min(1).optional(),
  type: z.enum(secretTypes),
  name: z.string().min(1, "Please enter a secret name."),
  description: z.string().max(255).nullish(),
  keys: z.array(keyValueSchema),
})

export type CreateSecretParams = z.infer<typeof createSecretSchema>

export const secretSchema = z.object({
  id: z.string().min(1).optional(),
  type: z.enum(secretTypes),
  name: z.string().min(1, "Please enter a secret name."),
  description: z.string().max(255).nullish(),
  keys: z.array(z.string()),
})

export type Secret = z.infer<typeof secretSchema>
