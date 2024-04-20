/**
 * @fileoverview This file contains methods for the workflow graph
 * .
 */

import { z } from "zod"

export const stringToJSONSchema = z.string().transform((str, ctx) => {
  try {
    return JSON.parse(str)
  } catch (e) {
    ctx.addIssue({ code: "custom", message: "Invalid JSON" })
    return z.NEVER
  }
})

export const stringArray = z
  .array(z.string().min(1, { message: "Strings cannot be empty" }))
  .min(1, { message: "List cannot be empty" })

// General schemas

export const keyValueSchema = z.object({
  key: z.string().min(1, "Please enter a key."),
  value: z.string().min(1, "Please enter a value."),
})

export const tagSchema = z.object({
  tag: z.string().min(1, "Please enter a tag."),
  value: z.string().min(1, "Please enter a value."),
  is_ai_generated: z.boolean().default(false),
})
