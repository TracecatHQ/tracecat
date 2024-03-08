import { ActionType } from "@/types"
import { z } from "zod"

const jsonPayload = z
  .string()
  .optional()
  .transform((val) => {
    try {
      return val ? JSON.parse(val) : {}
    } catch (error) {
      // TODO: Handle error on SAVE only
      console.error("Error parsing payload:", error)
      return {}
    }
  })

const stringArray = z
  .string()
  .optional()
  .transform((val) => (val ? val.split(",") : []))
  .pipe(z.string().array())

const WebhookActionSchema = z.object({
  path: z.string(), // The webhook ID
  secret: z.string().optional(), // The webhook secret
  url: z.string().url(), // Whitelist of supported URL formats
  method: z.enum(["GET", "POST"]),
})

const HTTPRequestActionSchema = z.object({
  url: z.string().url(),
  method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]),
  headers: jsonPayload.optional(),
  payload: jsonPayload,
})

const SendEmailActionSchema = z.object({
  // recipients is a comma delimited list of email addresses. Pasrse it into an array
  recipients: z
    .string()
    .transform((val) => (val ? val.split(",") : []))
    .pipe(z.string().email().array()),
  subject: z.string(),
  contents: z.string(),
})

const conditionCompareActionSubtypes = [
  "less_than",
  "less_than_or_equal_to",
  "greater_than",
  "greater_than_or_equal_to",
  "equal_to",
  "not_equal_to",
] as const

const ConditionCompareActionSchema = z.object({
  subtype: z.enum(conditionCompareActionSubtypes),
  lhs: z.string(),
  rhs: z.string(),
})
const conditionRegexActionSubtypes = ["regex_match", "regex_not_match"] as const
const ConditionRegexActionSchema = z.object({
  subtype: z.enum(conditionRegexActionSubtypes),
  pattern: z.string(),
  text: z.string(),
})
const conditionMembershipActionSubtypes = [
  "contains",
  "does_not_contain",
] as const
const ConditionMembershipActionSchema = z.object({
  subtype: z.enum(conditionMembershipActionSubtypes),
  item: z.string(),
  container: z.string(),
})

const LLMTranslateActionSchema = z.object({
  message: z.string(),
  from_language: z.string(),
  to_language: z.string(),
  response_schema: jsonPayload.optional(),
})

const LLMExtractActionSchema = z.object({
  message: z.string(),
  groups: stringArray,
  response_schema: jsonPayload.optional(),
})

const LLMLabelTaskActionSchema = z.object({
  message: z.string(),
  labels: stringArray,
  response_schema: jsonPayload.optional(),
})

const LLMChoiceTaskActionSchema = z.object({
  message: z.string(),
  choices: stringArray,
  response_schema: jsonPayload.optional(),
})

const LLMSummarizeTaskActionSchema = z.object({
  message: z.string(),
  summary: z.string(),
  response_schema: jsonPayload.optional(),
})

type ActionFieldType = "input" | "select" | "textarea"
export interface ActionFieldOption {
  type: ActionFieldType
  options?: readonly string[]
}

interface ActionFieldSchema {
  [key: string]: ActionFieldOption
}

export type ActionFieldSchemas = {
  [actionType in ActionType]: ActionFieldSchema
}

const actionFieldSchemas: Partial<ActionFieldSchemas> = {
  webhook: {
    url: { type: "input" },
    method: {
      type: "select",
      options: ["GET", "POST"],
    },
    path: { type: "input" },
    secret: { type: "input" },
  },
  http_request: {
    url: { type: "input" },
    method: {
      type: "select",
      options: ["GET", "POST", "PUT", "PATCH", "DELETE"],
    },
    headers: { type: "textarea" },
    payload: { type: "textarea" },
  },
  send_email: {
    recipients: { type: "input" },
    subject: { type: "input" },
    contents: { type: "textarea" },
  },
  "condition.compare": {
    subtype: {
      type: "select",
      options: conditionCompareActionSubtypes,
    },
    lhs: { type: "input" },
    rhs: { type: "input" },
  },
  "condition.regex": {
    subtype: {
      type: "select",
      options: conditionRegexActionSubtypes,
    },
    pattern: { type: "input" },
    text: { type: "textarea" },
  },
  "condition.membership": {
    subtype: {
      type: "select",
      options: conditionMembershipActionSubtypes,
    },
    item: { type: "input" },
    container: { type: "input" },
  },
  "llm.translate": {
    // TODO: Replace with supported languages and Command input
    message: { type: "textarea" },
    from_language: { type: "input" },
    to_language: { type: "input" },
    response_schema: { type: "textarea" },
  },
  "llm.extract": {
    message: { type: "textarea" },
    // TODO: Replace with Command input and ability to add to list
    groups: { type: "input" }, // Assuming a comma-separated string to be transformed into an array
    response_schema: { type: "textarea" },
  },
  "llm.label": {
    // TODO: Replace with Command input and ability to add to list
    message: { type: "textarea" },
    labels: { type: "input" }, // Assuming a comma-separated string to be transformed into an array
    response_schema: { type: "textarea" },
  },
  "llm.choice": {
    message: { type: "textarea" },
    choices: { type: "input" },
    response_schema: { type: "textarea" },
  },
  "llm.summarize": {
    message: { type: "textarea" },
    summary: { type: "textarea" },
    response_schema: { type: "textarea" },
  },
}

export const getActionSchema = (actionType: ActionType) => {
  switch (actionType) {
    case "http_request":
      return {
        actionSchema: HTTPRequestActionSchema,
        actionFieldSchema: actionFieldSchemas.http_request,
      }
    case "webhook":
      return {
        actionSchema: WebhookActionSchema,
        actionFieldSchema: actionFieldSchemas.webhook,
      }
    case "send_email":
      return {
        actionSchema: SendEmailActionSchema,
        actionFieldSchema: actionFieldSchemas.send_email,
      }
    case "condition.compare":
      return {
        actionSchema: ConditionCompareActionSchema,
        actionFieldSchema: actionFieldSchemas["condition.compare"],
      }
    case "condition.regex":
      return {
        actionSchema: ConditionRegexActionSchema,
        actionFieldSchema: actionFieldSchemas["condition.regex"],
      }
    case "condition.membership":
      return {
        actionSchema: ConditionMembershipActionSchema,
        actionFieldSchema: actionFieldSchemas["condition.membership"],
      }
    case "llm.translate":
      return {
        actionSchema: LLMTranslateActionSchema,
        actionFieldSchema: actionFieldSchemas["llm.translate"],
      }
    case "llm.extract":
      return {
        actionSchema: LLMExtractActionSchema,
        actionFieldSchema: actionFieldSchemas["llm.extract"],
      }
    case "llm.label":
      return {
        actionSchema: LLMLabelTaskActionSchema,
        actionFieldSchema: actionFieldSchemas["llm.label"],
      }
    case "llm.choice":
      return {
        actionSchema: LLMChoiceTaskActionSchema,
        actionFieldSchema: actionFieldSchemas["llm.choice"],
      }
    case "llm.summarize":
      return {
        actionSchema: LLMSummarizeTaskActionSchema,
        actionFieldSchema: actionFieldSchemas["llm.summarize"],
      }
    default:
      return null // No schema or UI hints available for the given action type
  }
}
