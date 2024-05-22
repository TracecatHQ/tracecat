import { useQuery } from "@tanstack/react-query"
import { type JSONType } from "ajv"
import type {
  JSONSchemaType,
  PropertiesSchema,
} from "ajv/dist/types/json-schema"
import { z } from "zod"

import { strAsDate } from "@/types/schemas"
import { client } from "@/lib/api"

//////////////////////////////////////////
// UDF related
//////////////////////////////////////////

export const UDFSchema = z.object({
  args: z.record(z.string(), z.unknown()),
  rtype: z.record(z.string(), z.unknown()),
  description: z.string(),
  key: z.string(),
  version: z.string().nullable(),
  metadata: z.record(z.string(), z.unknown()).nullable(),
  namespace: z.string(),
  secrets: z.array(z.string()).nullable(),
})

export type UDF = z.infer<typeof UDFSchema>

export const UDFSpecSchema = z.object({
  owner_id: z.string(),
  created_at: strAsDate,
  updated_at: strAsDate,
  id: z.string(),
  description: z.string(),
  namespace: z.string(),
  key: z.string(),
  version: z.string().nullable(),
  meta: z.record(z.string(), z.unknown()).nullable(),
  json_schema: UDFSchema,
})
export type UDFSpec = z.infer<typeof UDFSpecSchema>

export async function fetchAllUDFs(namespace?: string): Promise<UDF[]> {
  let path = "/udfs"
  if (namespace) {
    path += `?namespace=${namespace}`
  }
  const response = await client.get<UDF[]>(path)
  const udfspecs = await z.array(UDFSpecSchema).parseAsync(response.data)
  return udfspecs.map((u) => u.json_schema)
}

export async function fetchUDF(key: string, namespace?: string): Promise<UDF> {
  let path = `/udfs/${key}`
  if (namespace) {
    path += `?namespace=${namespace}`
  }
  const response = await client.get<UDF>(path)
  const udfspec = await UDFSpecSchema.parseAsync(response.data)
  console.log("udfspec", udfspec)
  return udfspec.json_schema
}

/**
 * We might not need dto list all UDFs, but just need the keys
 * @param namespace
 * @returns
 */
export function useUDFs(namespace?: string) {
  const {
    data: udfs,
    isLoading,
    error,
  } = useQuery<UDF[], Error>({
    queryKey: ["udfs"],
    queryFn: async () => await fetchAllUDFs(namespace),
  })
  if (isLoading) {
    return { udfs: [], isLoading, error }
  }
  return { udfs, isLoading, error }
}

/**
 *
 * @param key
 * @returns Hook that has the UDF schema that will be passed into AJV
 */
export function useUDFFormSchema(
  key: string,
  namespace?: string
): {
  formSchema?: JSONSchemaType<any>
  formConfig?: FormConfig
  udf?: UDF
  isLoading: boolean
} {
  const { data: udf, isLoading } = useQuery({
    queryKey: ["udf_field_config", key],
    queryFn: async ({ queryKey }) => {
      const [, key] = queryKey as [string, string]
      console.log("fetching UDF", key, namespace)
      return await fetchUDF(key, namespace)
    },
  })
  // Parse the schema and config

  if (!udf) {
    return {
      udf,
      isLoading,
    }
  }
  // Parse the UDF.
  // Since we're using AJV, it takes a JSONSchema directly as the field schema
  // We just have to parse out the field config
  const schema = udf.args as JSONSchemaType<any>
  const formSchema = generateFormSchema(schema)
  const formConfig = generateFormConfig(schema)
  return { formSchema, formConfig, udf, isLoading }
}

/**
 * Prepare the UDF schema for AJV
 * 1. For each required string field, add minLength clause
 * @param schema
 * @returns Processed JSONSchema object for AJV + react hook form resolver
 */
export function generateFormSchema(
  schema: JSONSchemaType<any>
): JSONSchemaType<any> {
  // Copy the schema
  const processedSchema = structuredClone(schema)
  // For each type, add minLenght constraints.
  // e.g. if a string type is required, add minLength: 1
  // if array type is required, add minItems: 1
  for (const key of schema.required || []) {
    const type = schema?.properties[key]?.type as JSONType
    switch (type) {
      case "string":
        processedSchema.properties[key].minLength = 1
        break
      case "array":
        processedSchema.properties[key].minItems = 1
        break
    }
  }
  return processedSchema
}

//////////////////////////////////////////
// Form + Field config related
//////////////////////////////////////////

/**
 * Maps to the UI field type in the form
 */
export type FieldType =
  | "input"
  | "select"
  | "textarea"
  | "json"
  | "array"
  | "flatkv"
  | "datetime"

/**
 * All field configurations for the form.
 */
export interface FormConfig {
  [key: string]: FieldConfig
}

/**
 * Field options for the form
 */
export type FieldConfig = (
  | {
      kind: "input" | "textarea" | "json" | "array"
      placeholder?: string
    }
  | {
      kind: "select"
      options: any[]
    }
  | {
      kind: "flatkv"
      keyPlaceholder: string
      valuePlaceholder: string
    }
  | {
      kind: "datetime"
      inputType: "datetime-local" | "date" | "time" | "month" | "week"
    }
) & {
  // These fields are controlled from the backend
  dtype: JSONType
  default?: any
  disabled?: boolean
  optional?: boolean
  copyable?: boolean
  inputType?: React.HTMLInputTypeAttribute
}

/**
 * Generate the field config for the UDF.
 * This determines how the fields are rendered in the form
 * We only use this for the input fields.
 *
 * Logic:
 * 1. Get a base ActionFieldOption, which has the 'type' (ui), 'dtype' fields.
 *
 * @param schema
 * @returns Our custom field config to render the form UI elements
 */
export function generateFormConfig(schema: JSONSchemaType<any>): FormConfig {
  let formConfig: FormConfig = {}
  //
  const properties = schema.properties as PropertiesSchema<any>
  Object.entries(properties).forEach(([key, rawObj]) => {
    // Depending on the type, we set the field config
    const propertyObj = rawObj as JSONSchemaType<any>
    const options = getFieldOptions(propertyObj)

    const isOptional = schema.required?.includes(key) ? false : true
    formConfig[key] = {
      ...(options || ({} as FieldConfig)),
      optional: isOptional,
      default: propertyObj?.default ?? undefined,
    }
  })
  return formConfig
}

/**
 * Generate the field config for the UDF.
 *
 * @param propertyObj
 * @returns Field configuration for a form field
 */
export function getFieldOptions(propertyObj: JSONSchemaType<any>): FieldConfig {
  if (!propertyObj.type) {
    if (propertyObj?.anyOf) return getAnyOfFieldOptions(propertyObj)
    throw new Error("Could not find matching object property.")
  }
  const placeholder = getPlaceholder(propertyObj)
  switch (propertyObj.type as JSONType) {
    case "string":
      return handleStringField(propertyObj, placeholder)
    case "number":
      return {
        kind: "input",
        placeholder,
        dtype: "number",
      }
    case "integer":
      return { kind: "input", placeholder, dtype: "integer" }
    case "object":
      return {
        kind: "flatkv",
        keyPlaceholder: "Key",
        valuePlaceholder: "Value",
        dtype: "object",
      }
    case "array":
      return { kind: "array", placeholder, dtype: "array" }
    case "boolean":
      return {
        kind: "select",
        options: ["true", "false"],
        dtype: "boolean",
      }
    default:
      // TODO: Handle this differently if needed.
      // Not sure what 'null' type should return.
      throw new Error("Could not match obj.type")
  }
}
function getPlaceholder(propertyObj: JSONSchemaType<any>): string {
  return propertyObj.description
    ? `${propertyObj.description} (${propertyObj.type})`
    : `${propertyObj.type} field.`
}

export function handleStringField(
  propertyObj: JSONSchemaType<any>,
  placeholder: string
): FieldConfig {
  if (propertyObj?.enum) {
    // If we have an enum, we treat it as a select field
    return {
      kind: "select",
      options: propertyObj.enum as string[],
      dtype: "string",
    }
  } else if (propertyObj?.format === "date-time") {
    // If we have a date-time format, we treat it as a date-time field
    // By default we'll use the datetime-local input type
    return { kind: "datetime", inputType: "datetime-local", dtype: "string" }
  } else {
    // Otherwise, we treat it as a string input field
    return { kind: "input", placeholder, dtype: "string" }
  }
}

export function getAnyOfFieldOptions(obj: JSONSchemaType<any>): FieldConfig {
  // If we have an 'anyOf' type, we need to flatten it
  // Also, not sure if it even makes sense to have more than 1 type besides 'null' in 'anyOf'
  // If we have an 'anyOf' type, we need to flatten it.
  // Here's the logic:
  // Even though we recommended that the UDF signatures are constructed without null unions,
  // we still need to handle this case.
  // 2. If the length is 2 and includes 'null', return the field options
  // 3. If the length is > 2, throw error (means there are > 1 types)

  // Flatten the anyOf array
  const anyOf = obj.anyOf as JSONSchemaType<any>[]
  const types = anyOf.map((type) => type.type as JSONType)
  if (types.length === 2 && types.includes("null")) {
    const nonNullType = anyOf.find(
      (type) => type.type !== "null"
    ) as JSONSchemaType<any>
    // Treat it as a non-null type, but guarantee that it's optional
    return getFieldOptions(nonNullType)
  }

  throw new Error("Could not match anyOf type")
}
