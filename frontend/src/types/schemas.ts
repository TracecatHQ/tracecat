import { z } from "zod"

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

export const secretKeyValueSchema = z.object({
  key: z.string().min(1, "Please enter a key."),
  value: z.string().min(1, "Please enter a value."),
})
