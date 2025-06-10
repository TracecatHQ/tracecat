import type {
  JSONSchema7,
  JSONSchema7Definition,
  JSONSchema7Type,
} from "json-schema"

export type TcJsonSchemaExtra = {
  "x-tracecat-component"?: {
    component_id: string
    [key: string]: unknown
  }
}

export type TracecatJsonSchema = JSONSchema7 & TcJsonSchemaExtra
export type TracecatJsonSchemaDefinition = JSONSchema7Definition &
  TcJsonSchemaExtra
export type TracecatJsonSchemaType = JSONSchema7Type & TcJsonSchemaExtra

export const TRACECAT_COMPONENT_KEY = "x-tracecat-component" as const
export function isTracecatJsonSchema(
  schema: unknown
): schema is TracecatJsonSchema {
  return (
    typeof schema === "object" &&
    schema !== null &&
    TRACECAT_COMPONENT_KEY in schema
  )
}
