import {
  TRACECAT_COMPONENT_KEY,
  TracecatJsonSchema,
  TracecatJsonSchemaDefinition,
  TracecatJsonSchemaType,
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
  return (value.type || "unknown") as string
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
