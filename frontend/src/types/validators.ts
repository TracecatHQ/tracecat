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
