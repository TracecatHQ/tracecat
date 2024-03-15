import { ActionType } from "@/types"
import { z, ZodObject, ZodUnion } from "zod"

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

// const stringArray = z
//   .string()
//   .optional()
//   .transform((val) => (val ? val.split(",") : []))
//   .pipe(z.string().array())
//   .or(z.array(z.string()))
const stringArray = z.string().array()

const WebhookActionSchema = z.object({
  path: z.string(), // The webhook ID
  secret: z.string().optional(), // The webhook secret
  url: z.string().url(), // Whitelist of supported URL formats
  method: z.enum(["GET", "POST"]),
})

const HTTPRequestActionSchema = z.object({
  url: z.string(),
  method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]),
  headers: jsonPayload.optional(),
  payload: jsonPayload,
})

const SendEmailActionSchema = z.object({
  // recipients is a comma delimited list of email addresses. Pasrse it into an array
  recipients: z.string().array(),
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

const OpenCaseActionSchema = z.object({
  title: z.string(),
  payload: jsonPayload,
  malice: z.enum(["malicious", "benign"]),
  status: z.enum(["open", "closed", "in_progress", "reported", "escalated"]),
  priority: z.enum(["low", "medium", "high", "critical"]),
  context: jsonPayload.optional(),
  action: z.string().optional(),
  suppression: jsonPayload.optional(),
})
export const baseActionSchema = z.object({
  title: z.string(),
  description: z.string(),
})
export type BaseActionForm = z.infer<typeof baseActionSchema>

// Ugly, but recommended by the author
// https://github.com/colinhacks/zod/issues/147#issuecomment-694065226
const dynamicSubActionFormSchema = z.union([
  baseActionSchema.merge(WebhookActionSchema),
  baseActionSchema.merge(HTTPRequestActionSchema),
  baseActionSchema.merge(SendEmailActionSchema),
  baseActionSchema.merge(ConditionCompareActionSchema),
  baseActionSchema.merge(ConditionRegexActionSchema),
  baseActionSchema.merge(ConditionMembershipActionSchema),
  baseActionSchema.merge(LLMTranslateActionSchema),
  baseActionSchema.merge(LLMExtractActionSchema),
  baseActionSchema.merge(LLMLabelTaskActionSchema),
  baseActionSchema.merge(LLMChoiceTaskActionSchema),
  baseActionSchema.merge(LLMSummarizeTaskActionSchema),
  baseActionSchema.merge(OpenCaseActionSchema),
])
export type DynamicSubActionForm = z.infer<typeof dynamicSubActionFormSchema>
export type ActionFieldType = "input" | "select" | "textarea" | "json" | "array"

export interface ActionFieldOption {
  type: ActionFieldType
  options?: readonly string[]
}

export interface ActionFieldSchema {
  [key: string]: ActionFieldOption
}

export type AllActionFieldSchemas = {
  [actionType in ActionType]?: ActionFieldSchema
}

const actionSchemaMap: Partial<
  Record<ActionType, z.ZodType<DynamicSubActionForm>>
> = {
  http_request: baseActionSchema.merge(HTTPRequestActionSchema),
  webhook: baseActionSchema.merge(WebhookActionSchema),
  send_email: baseActionSchema.merge(SendEmailActionSchema),
  "condition.compare": baseActionSchema.merge(ConditionCompareActionSchema),
  "condition.regex": baseActionSchema.merge(ConditionRegexActionSchema),
  "condition.membership": baseActionSchema.merge(
    ConditionMembershipActionSchema
  ),
  "llm.translate": baseActionSchema.merge(LLMTranslateActionSchema),
  "llm.extract": baseActionSchema.merge(LLMExtractActionSchema),
  "llm.label": baseActionSchema.merge(LLMLabelTaskActionSchema),
  "llm.choice": baseActionSchema.merge(LLMChoiceTaskActionSchema),
  "llm.summarize": baseActionSchema.merge(LLMSummarizeTaskActionSchema),
  open_case: baseActionSchema.merge(OpenCaseActionSchema),
}

export const getActionSchema = (actionType: ActionType) => {
  return {
    actionSchema: actionSchemaMap[actionType],
    actionFieldSchema: actionFieldSchemas[actionType],
  }
}
const actionFieldSchemas: Partial<AllActionFieldSchemas> = {
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
    headers: { type: "json" },
    payload: { type: "json" },
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
    groups: { type: "array" }, // Assuming a comma-separated string to be transformed into an array
    response_schema: { type: "json" },
  },
  "llm.label": {
    // TODO: Replace with Command input and ability to add to list
    message: { type: "textarea" },
    labels: { type: "array" }, // Assuming a comma-separated string to be transformed into an array
    response_schema: { type: "json" },
  },
  "llm.choice": {
    message: { type: "textarea" },
    choices: { type: "array" },
    response_schema: { type: "json" },
  },
  "llm.summarize": {
    message: { type: "textarea" },
    summary: { type: "textarea" },
    response_schema: { type: "json" },
  },
  open_case: {
    title: { type: "input" },
    payload: { type: "json" },
    malice: {
      type: "select",
      options: ["malicious", "benign"],
    },
    status: {
      type: "select",
      options: ["open", "closed", "in_progress", "reported", "escalated"],
    },
    priority: {
      type: "select",
      options: ["low", "medium", "high", "critical"],
    },
    context: { type: "json" },
    action: { type: "textarea" },
    suppression: { type: "json" },
  },
}
