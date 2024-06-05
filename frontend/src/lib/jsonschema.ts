import { JSONSchema7 } from "json-schema"

export interface JSONSchemaTableRow {
  parameter: string
  type: string
  default: any
  description: string
  constraints: string
}

export function generateSchemaDefault(schema: JSONSchema7): any {
  if (schema.default !== undefined && schema.default !== null) {
    return schema.default
  }
  switch (schema.type) {
    case "object": {
      const obj: { [key: string]: any } = {}
      if (schema.properties) {
        for (const key in schema.properties) {
          obj[key] = generateSchemaDefault(
            schema.properties[key] as JSONSchema7
          )
        }
      }
      return obj
    }
    case "array": {
      if (Array.isArray(schema.items)) {
        return schema.items.map((item) =>
          generateSchemaDefault(item as JSONSchema7)
        )
      } else if (schema.items) {
        return [generateSchemaDefault(schema.items as JSONSchema7)]
      } else {
        return []
      }
    }
    case "string":
      return ""
    case "number":
      return 0
    case "boolean":
      return false
    case "null":
      return null
    default:
      if (schema.anyOf && schema.anyOf.length > 0) {
        return generateSchemaDefault(schema.anyOf[0] as JSONSchema7)
      }
      if (schema.oneOf && schema.oneOf.length > 0) {
        return generateSchemaDefault(schema.oneOf[0] as JSONSchema7)
      }
      if (schema.allOf && schema.allOf.length > 0) {
        return generateSchemaDefault(schema.allOf[0] as JSONSchema7)
      }
      return schema.default || null
  }
}

export function transformJsonSchemaToTableRows(
  schema: JSONSchema7
): JSONSchemaTableRow[] {
  const properties = schema.properties || {}
  const required = schema.required || []

  const rows: JSONSchemaTableRow[] = Object.entries(properties).map(
    ([key, value]) => ({
      parameter: `${key}${required.includes(key) ? " *" : ""}`,
      type: getType(value),
      default: (value as any).default ?? "-",
      description: (value as any).description ?? "",
      constraints: getConstraints(value),
    })
  )
  return rows
}

export function getType(value: any): string {
  if (value.anyOf) {
    return value.anyOf.map((item: any) => item.type).join(" | ")
  }
  if (value.enum) {
    return value.enum.join(" | ")
  }
  return value.type || "unknown"
}

export function getConstraints(value: any): string {
  const constraints = []

  // Exclude these standard keys from constraints
  const excludeKeys = ["type", "description", "default", "title", "anyOf"]

  // Iterate through the keys of the value object
  for (const key in value) {
    if (excludeKeys.includes(key)) continue
    const cons = value[key]
    if (key === "additionalProperties") {
      constraints.push(`additionalProperties: ${getType(cons)}`)
    } else if (Array.isArray(cons)) {
      constraints.push(`${key}: [${cons.join(", ")}]`)
    } else if (typeof cons === "object" && cons !== null) {
      constraints.push(`${key}: ${JSON.stringify(cons)}`)
    } else {
      constraints.push(`${key}: ${cons}`)
    }
  }

  return constraints.join("\n")
}
