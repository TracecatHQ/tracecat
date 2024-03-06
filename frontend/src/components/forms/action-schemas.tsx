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

const LLMTranslateActionSchema = z.object({
  text: z.string(),
  from_language: z.string(),
  to_language: z.string(),
})

const LLMExtractActionSchema = z.object({
  text: z.string(),
  groups: z.array(z.string()),
})

const LLMLabelTaskActionSchema = z.object({
  text: z.string(),
  labels: z.array(z.string()),
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
  Translate: {
    // TODO: Replace with supported languages and Command input
    text: { type: "Textarea" },
    from_language: { type: "Input" },
    to_language: { type: "Input" },
  },
  Extract: {
    text: { type: "Textarea" },
    // TODO: Replace with Command input and ability to add to list
    groups: { type: "Input" }, // Assuming a comma-separated string to be transformed into an array
  },
  Label: {
    // TODO: Replace with Command input and ability to add to list
    text: { type: "Textarea" },
    labels: { type: "Input" }, // Assuming a comma-separated string to be transformed into an array
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
    default:
      return null // No schema or UI hints available for the given action type
  }
}
