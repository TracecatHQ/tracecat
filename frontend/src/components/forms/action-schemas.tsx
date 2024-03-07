import { z } from "zod"

const WebhookActionSchema = z.object({
  url: z.string().url(),
  method: z.enum(["GET", "POST"]),
})

const HTTPRequestActionSchema = z.object({
  url: z.string().url(),
  method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]),
  headers: z
    .string()
    .optional()
    .transform((val) => {
      try {
        return val ? JSON.parse(val) : {}
      } catch (error) {
        // TODO: Handle error on SAVE only
        return {}
      }
    }),
  payload: z
    .string()
    .optional()
    .transform((val) => {
      try {
        return val ? JSON.parse(val) : {}
      } catch (error) {
        // TODO: Handle error on SAVE only
        return {}
      }
    }),
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

const LLMTranslateActionSchema = z.object({
  message: z.string(),
  from_language: z.string(),
  to_language: z.string(),
})

const LLMExtractActionSchema = z.object({
  message: z.string(),
  groups: z
    .string()
    .optional()
    .transform((val) => (val ? val.split(",") : []))
    .pipe(z.string().array()),
})

const LLMLabelTaskActionSchema = z.object({
  message: z.string(),
  labels: z
    .string()
    .optional()
    .transform((val) => (val ? val.split(",") : []))
    .pipe(z.string().array()),
})

const LLMChoiceTaskActionSchema = z.object({
  message: z.string(),
  choices: z
    .string()
    .optional()
    .transform((val) => (val ? val.split(",") : []))
    .pipe(z.string().array()),
})

const LLMSummarizeTaskActionSchema = z.object({
  message: z.string(),
  summary: z.string(),
})

interface ActionFieldOption {
  type: "Input" | "Select" | "Textarea"
  options?: string[]
}

interface ActionFieldSchema {
  [key: string]: ActionFieldOption
}

export interface ActionFieldSchemas {
  [actionType: string]: ActionFieldSchema
}

const actionFieldSchemas: ActionFieldSchemas = {
  Webhook: {
    url: { type: "Input" },
    method: {
      type: "Select",
      options: ["GET", "POST"],
    },
  },
  "HTTP Request": {
    url: { type: "Input" },
    method: {
      type: "Select",
      options: ["GET", "POST", "PUT", "PATCH", "DELETE"],
    },
    headers: { type: "Textarea" },
    payload: { type: "Textarea" },
  },
  "Send Email": {
    recipients: { type: "Input" },
    subject: { type: "Input" },
    contents: { type: "Textarea" },
  },
  Translate: {
    // TODO: Replace with supported languages and Command input
    message: { type: "Textarea" },
    from_language: { type: "Input" },
    to_language: { type: "Input" },
  },
  Extract: {
    message: { type: "Textarea" },
    // TODO: Replace with Command input and ability to add to list
    groups: { type: "Input" }, // Assuming a comma-separated string to be transformed into an array
  },
  Label: {
    // TODO: Replace with Command input and ability to add to list
    message: { type: "Textarea" },
    labels: { type: "Input" }, // Assuming a comma-separated string to be transformed into an array
  },
  Choice: {
    message: { type: "Textarea" },
    choices: { type: "Input" },
  },
  Summarize: {
    message: { type: "Textarea" },
    summary: { type: "Textarea" },
  },
}

export const getActionSchema = (actionType: string) => {
  switch (actionType) {
    case "HTTP Request":
      return {
        actionSchema: HTTPRequestActionSchema,
        actionFieldSchema: actionFieldSchemas["HTTP Request"],
      }
    case "Webhook":
      return {
        actionSchema: WebhookActionSchema,
        actionFieldSchema: actionFieldSchemas["Webhook"],
      }
    case "Send Email":
      return {
        actionSchema: SendEmailActionSchema,
        actionFieldSchema: actionFieldSchemas["Send Email"],
      }
    case "Translate":
      return {
        actionSchema: LLMTranslateActionSchema,
        actionFieldSchema: actionFieldSchemas["Translate"],
      }
    case "Extract":
      return {
        actionSchema: LLMExtractActionSchema,
        actionFieldSchema: actionFieldSchemas["Extract"],
      }
    case "Label":
      return {
        actionSchema: LLMLabelTaskActionSchema,
        actionFieldSchema: actionFieldSchemas["Label"],
      }
    case "Choice":
      return {
        actionSchema: LLMChoiceTaskActionSchema,
        actionFieldSchema: actionFieldSchemas["Choice"],
      }
    case "Summarize":
      return {
        actionSchema: LLMSummarizeTaskActionSchema,
        actionFieldSchema: actionFieldSchemas["Summarize"],
      }
    default:
      return null // No schema or UI hints available for the given action type
  }
}
