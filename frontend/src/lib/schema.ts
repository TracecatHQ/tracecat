import type {
  JSONSchema7,
  JSONSchema7Definition,
  JSONSchema7Type,
} from "json-schema"
import type { EditorComponent } from "@/client"

/* expression component */

export type ExpressionComponent = {
  component_id: "expression"
}

export type TracecatEditorComponent = EditorComponent | ExpressionComponent
export type TracecatComponentId = NonNullable<
  TracecatEditorComponent["component_id"]
>

export type TracecatJsonSchema = JSONSchema7 & TcJsonSchemaExtra
export type TracecatJsonSchemaDefinition = JSONSchema7Definition &
  TcJsonSchemaExtra
export type TracecatJsonSchemaType = JSONSchema7Type & TcJsonSchemaExtra
export const TRACECAT_COMPONENT_KEY = "x-tracecat-component" as const

export type TcJsonSchemaExtra = {
  [TRACECAT_COMPONENT_KEY]?: TracecatEditorComponent[]
}

export function isTracecatJsonSchema(
  schema: unknown
): schema is TracecatJsonSchema {
  return (
    typeof schema === "object" &&
    schema !== null &&
    TRACECAT_COMPONENT_KEY in schema
  )
}

// Helper function to get components as array
/**
 * Returns the Tracecat components array from the schema.
 * If the component key is not present or not an array, returns an empty array.
 * Only returns the value if it is an array.
 *
 * @param schema - The TracecatJsonSchema object to extract components from.
 * @returns An array of component objects with at least a component_id.
 */
/**
 * Type guard to check if an object is a valid Tracecat component.
 * A valid component must be a non-null object with a string 'component_id' property.
 *
 * @param item - The item to check.
 * @returns True if the item is a valid Tracecat component, false otherwise.
 */
export function isTracecatComponent(
  item: unknown
): item is TracecatEditorComponent {
  return (
    typeof item === "object" &&
    item !== null &&
    "component_id" in item &&
    typeof (item as { component_id: unknown }).component_id === "string"
  )
}

/**
 * Returns the Tracecat components array from the schema.
 * If the component key is not present or not an array, returns an empty array.
 * Only returns the value if it is an array of valid Tracecat components.
 *
 * @param schema - The TracecatJsonSchema object to extract components from.
 * @returns An array of component objects with at least a component_id.
 */
export function getTracecatComponents(
  schema: TracecatJsonSchema
): TracecatEditorComponent[] {
  const component = schema[TRACECAT_COMPONENT_KEY]
  if (Array.isArray(component)) {
    // Use the type guard to filter valid components
    return component.filter(isTracecatComponent)
  }
  return []
}

// Helper function to check if schema has multiple components
export function hasMultipleComponents(schema: TracecatJsonSchema): boolean {
  const component = schema[TRACECAT_COMPONENT_KEY]
  return Array.isArray(component) && component.length > 1
}
