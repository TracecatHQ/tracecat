import { JSONSchema7 } from "json-schema"
import { z } from "zod"

import {
  TRACECAT_COMPONENT_KEY,
  type TracecatJsonSchema,
  type TracecatJsonSchemaDefinition,
  type TracecatJsonSchemaType,
} from "@/lib/schema"

export interface JSONSchemaParam {
  parameter: string
  type: string
  default: TracecatJsonSchemaType
  description: string
  constraints: string
  required: boolean
}

export function jsonSchemaToParams(
  schema: TracecatJsonSchema
): JSONSchemaParam[] {
  const properties = schema.properties || {}
  const required = schema.required || []

  const rows: JSONSchemaParam[] = Object.entries(properties).map(
    ([key, value]) => {
      const jsonSchemaValue = value as TracecatJsonSchema
      const { default: defaultValue, description } = jsonSchemaValue
      return {
        parameter: key,
        type: getType(jsonSchemaValue),
        default: defaultValue ? JSON.stringify(defaultValue) : "-",
        description: description ?? "",
        constraints: getConstraints(jsonSchemaValue),
        required: required.includes(key),
      }
    }
  )
  return rows
}

export function getType(value: TracecatJsonSchema): string {
  // Handle anyOf
  if (value.anyOf) {
    return value.anyOf.map((item) => getTypeFromDefinition(item)).join(" | ")
  }

  // Handle oneOf
  if (value.oneOf) {
    return value.oneOf.map((item) => getTypeFromDefinition(item)).join(" | ")
  }

  // Handle allOf
  if (value.allOf) {
    return value.allOf.map((item) => getTypeFromDefinition(item)).join(" & ")
  }

  // Handle enum
  if (value.enum) {
    return value.enum.map((v) => `"${v}"`).join(" | ")
  }

  // Handle type as array (multiple types)
  if (Array.isArray(value.type)) {
    return value.type.join(" | ")
  }

  // Handle primitive types
  return (value.type || "any") as string
}

// Helper function to get type from JSONSchema7Definition
function getTypeFromDefinition(
  definition: TracecatJsonSchemaDefinition
): string {
  if (typeof definition === "boolean") {
    throw new Error("Boolean type not supported")
  }
  return getType(definition)
}

export function getConstraints(value: TracecatJsonSchema): string {
  const constraints = []

  // Exclude these standard keys from constraints
  const excludeKeys = [
    "type",
    "description",
    "default",
    "title",
    "anyOf",
    TRACECAT_COMPONENT_KEY,
  ]

  // Iterate through the keys of the value object
  for (const key in value) {
    if (excludeKeys.includes(key)) continue
    const cons = value[key as keyof TracecatJsonSchema]
    if (key === "additionalProperties") {
      constraints.push(
        `additionalProperties: ${getType(cons as TracecatJsonSchema)}`
      )
    } else if (Array.isArray(cons)) {
      constraints.push(
        `${key}: [${cons.map((c) => JSON.stringify(c)).join(", ")}]`
      )
    } else if (typeof cons === "object" && cons !== null) {
      constraints.push(`${key}: ${JSON.stringify(cons)}`)
    } else {
      constraints.push(`${key}: ${cons}`)
    }
  }

  return constraints.join("\n")
}

export function jsonSchemaToZod(schema: JSONSchema7): z.ZodTypeAny {
  // Handle primitive types
  if (schema.type) {
    switch (schema.type) {
      case "string":
        let stringSchema = z.string()
        if (schema.minLength) {
          stringSchema = stringSchema.min(
            schema.minLength,
            `Minimum ${schema.minLength} characters`
          )
        }
        if (schema.maxLength) {
          stringSchema = stringSchema.max(
            schema.maxLength,
            `Maximum ${schema.maxLength} characters`
          )
        }
        if (schema.format === "email") {
          stringSchema = stringSchema.email("Invalid email format")
        }
        if (schema.format === "uri" || schema.format === "url") {
          stringSchema = stringSchema.url("Invalid URL format")
        }
        if (schema.enum) {
          return z.enum(schema.enum as [string, ...string[]])
        }
        return stringSchema

      case "number":
        let numberSchema = z.number()
        if (schema.minimum !== undefined) {
          numberSchema = numberSchema.min(
            schema.minimum,
            `Minimum value is ${schema.minimum}`
          )
        }
        if (schema.maximum !== undefined) {
          numberSchema = numberSchema.max(
            schema.maximum,
            `Maximum value is ${schema.maximum}`
          )
        }
        return numberSchema

      case "integer":
        let intSchema = z.number().int("Must be an integer")
        if (schema.minimum !== undefined) {
          intSchema = intSchema.min(
            schema.minimum,
            `Minimum value is ${schema.minimum}`
          )
        }
        if (schema.maximum !== undefined) {
          intSchema = intSchema.max(
            schema.maximum,
            `Maximum value is ${schema.maximum}`
          )
        }
        return intSchema

      case "boolean":
        return z.boolean()

      case "array":
        if (schema.items) {
          const itemSchema = jsonSchemaToZod(schema.items as TracecatJsonSchema)
          return z.array(itemSchema)
        }
        return z.array(z.unknown())

      case "object":
        if (schema.properties) {
          const shape: Record<string, z.ZodTypeAny> = {}
          const required = schema.required || []

          Object.entries(schema.properties).forEach(([key, prop]) => {
            if (typeof prop === "boolean") return
            let fieldSchema = jsonSchemaToZod(prop as TracecatJsonSchema)

            // Make optional if not required
            if (!required.includes(key)) {
              fieldSchema = fieldSchema.optional()
            }

            shape[key] = fieldSchema
          })

          return z.object(shape)
        }
        return z.object({})
    }
  }

  // Handle enum at root level
  if (schema.enum) {
    return z.enum(schema.enum as [string, ...string[]])
  }

  // Handle anyOf
  if (schema.anyOf) {
    const schemas = schema.anyOf.map((def) =>
      typeof def === "boolean" ? z.boolean() : jsonSchemaToZod(def)
    )
    return z.union(schemas as [z.ZodTypeAny, z.ZodTypeAny, ...z.ZodTypeAny[]])
  }

  // Handle oneOf (treat same as anyOf for validation purposes)
  if (schema.oneOf) {
    const schemas = schema.oneOf.map((def) =>
      typeof def === "boolean" ? z.boolean() : jsonSchemaToZod(def)
    )
    return z.union(schemas as [z.ZodTypeAny, z.ZodTypeAny, ...z.ZodTypeAny[]])
  }

  // Fallback
  return z.unknown()
}
