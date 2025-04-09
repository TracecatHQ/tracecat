import {
  JSONSchema7,
  JSONSchema7Definition,
  JSONSchema7Type,
} from "json-schema"

export interface JSONSchemaTableRow {
  parameter: string
  type: string
  default: JSONSchema7Type
  description: string
  constraints: string
  required: boolean
}

export function transformJsonSchemaToTableRows(
  schema: JSONSchema7
): JSONSchemaTableRow[] {
  const properties = schema.properties || {}
  const required = schema.required || []

  const rows: JSONSchemaTableRow[] = Object.entries(properties).map(
    ([key, value]) => {
      const jsonSchemaValue = value as JSONSchema7
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
  console.log(rows)
  return rows
}

export function getType(value: JSONSchema7): string {
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
function getTypeFromDefinition(definition: JSONSchema7Definition): string {
  if (typeof definition === "boolean") {
    throw new Error("Boolean type not supported")
  }
  return getType(definition)
}

export function getConstraints(value: JSONSchema7): string {
  const constraints = []

  // Exclude these standard keys from constraints
  const excludeKeys = ["type", "description", "default", "title", "anyOf"]

  // Iterate through the keys of the value object
  for (const key in value) {
    if (excludeKeys.includes(key)) continue
    const cons = value[key as keyof JSONSchema7]
    if (key === "additionalProperties") {
      constraints.push(`additionalProperties: ${getType(cons as JSONSchema7)}`)
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
