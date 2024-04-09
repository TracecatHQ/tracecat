import { useSession } from "@/providers/session"
import { Session } from "@supabase/supabase-js"
import { useQuery } from "@tanstack/react-query"
import { z } from "zod"

import {
  Integration,
  IntegrationPlatform,
  integrationSchema,
} from "@/types/schemas"
import { stringToJSONSchema } from "@/types/validators"
import { getAuthenticatedClient } from "@/lib/api"
import {
  ActionFieldConfig,
  ActionFieldOption,
} from "@/components/workspace/panel/action/schemas"

type ParameterType =
  | {
      type: "list" | "dict" | "union" | "tuple" | "enum"
      args: ParameterType[]
      value?: string | number
    }
  | {
      type: "bool" | "int" | "float" | "str" | "NoneType"
      value?: string | number
    }

type ParameterSpec = {
  name: string
  type: ParameterType
  default: { type: string; value: any } | null
}

type ZodTuple = [z.ZodTypeAny, ...z.ZodTypeAny[]]
type ZodUnion = [z.ZodTypeAny, z.ZodTypeAny, ...z.ZodTypeAny[]]
export async function fetchAllIntegrations(maybeSession: Session | null) {
  const client = getAuthenticatedClient(maybeSession)
  const response = await client.get<Integration[]>("/integrations")
  return z.array(integrationSchema).parse(response.data)
}

export async function fetchIntegration(
  maybeSession: Session | null,
  integrationKey: string
): Promise<Integration> {
  const client = getAuthenticatedClient(maybeSession)
  const response = await client.get<Integration>(
    `/integrations/${integrationKey}`
  )
  return integrationSchema.parse(response.data)
}

export function useIntegrations() {
  const session = useSession()

  const {
    data: integrations,
    isLoading,
    error,
  } = useQuery<Integration[], Error>({
    queryKey: ["integrations"],
    queryFn: async () => {
      if (!session) {
        console.error("Invalid session")
        throw new Error("Invalid session")
      }
      return await fetchAllIntegrations(session)
    },
  })
  return { integrations: integrations || [], isLoading, error }
}

export function parseSpec(parameters: any): {
  fieldSchema: z.ZodObject<Record<string, any>>
  fieldConfig: ActionFieldConfig
} {
  const fieldSchema = generateFieldSchema(parameters as ParameterSpec[])
  const fieldConfig = generateFieldConfig(parameters as ParameterSpec[])
  return { fieldSchema, fieldConfig }
}

// Simple template expression regex
const templateExpr = /^\{\{\s*.*?\s*\}\}$/
const numericExpr = /^\d+$/
const templateExprSchema = z
  .custom<string | number>(
    (value: any) => {
      return templateExpr.test(value) || numericExpr.test(value)
    },
    { message: "Invalid template expression" }
  )
  .transform((value: any) => {
    if (numericExpr.test(value)) {
      return Number(value)
    }
    return value
  })

function annotationToZod(annotation: ParameterType): z.ZodTypeAny {
  switch (annotation.type) {
    case "bool":
      return z.boolean()
    case "int":
      return templateExprSchema
    case "float":
      return templateExprSchema
    case "str":
      return z.string()
    case "NoneType":
      return z.null()
    case "list":
      return z
        .array(annotationToZod(annotation.args[0]))
        .min(1, { message: "List cannot be empty" })
    case "dict":
      return stringToJSONSchema
    case "union":
      // Optional falls under this case
      const unionItems = annotation.args.map(annotationToZod) as ZodUnion
      return z.union(unionItems)
    case "tuple":
      const tupleItems = annotation.args.map(annotationToZod) as ZodTuple
      return z.tuple(tupleItems)
    default:
      return z.unknown()
  }
}

function generateFieldSchema(
  args: ParameterSpec[]
): z.ZodObject<Record<string, any>> {
  const schemaObject: { [key: string]: z.ZodTypeAny } = {}
  args.forEach((arg) => {
    const schema = annotationToZod(arg.type)
    schemaObject[arg.name] = arg.default ? schema.optional() : schema
  })
  return z.object(schemaObject)
}

function generateFieldConfig(paramSpecs: ParameterSpec[]): ActionFieldConfig {
  const formSchema: { [key: string]: ActionFieldOption } = {}
  paramSpecs.forEach((paramSpec) => {
    const configParams = evaluateParameterType(paramSpec.type)
    const optionality = getOptionality(paramSpec)
    // if optionality includes a default value, indicade this in the placeholder
    if (optionality === "OPTIONAL_WITH_DEFAULT") {
      configParams.placeholder = `${configParams.placeholder} [default: ${paramSpec.default?.value} (${paramSpec.default?.type})]`
    }
    const formConfig = {
      optional: ["OPTIONAL", "OPTIONAL_WITH_DEFAULT"].includes(optionality),
      ...configParams,
    }
    formSchema[paramSpec.name] = formConfig
  })

  return formSchema
}
function getOptionality(
  param: ParameterSpec
): "REQUIRED" | "OPTIONAL" | "OPTIONAL_WITH_DEFAULT" {
  // Match python: param := (T | None = None)
  // Case 1: If it's a union type and one of the options is NoneType
  // and has a default value of NoneType -> OPTIONAL
  if (param.type.type === "union" && param.default?.type === "NoneType") {
    return "OPTIONAL"
  }
  // Case 2: If has type T and has a default value of T -> OPTIONAL_WITH_DEFAULT
  if (param.default) {
    return "OPTIONAL_WITH_DEFAULT"
  }
  // Case 3: None of the above -> REQUIRED
  return "REQUIRED"
}

function evaluateParameterType(param: ParameterType): ActionFieldOption {
  switch (param.type) {
    case "bool":
      return { type: "select", dtype: "bool", options: ["true", "false"] }
    case "int":
      return {
        type: "input",
        placeholder: "Integer type.",
      }
    case "float":
      return {
        type: "input",
        placeholder: "Float type.",
      }
    case "str":
      return { type: "input", placeholder: "String type." }
    case "union":
      const types = param.args.map((arg) => arg.type).join(", ")
      return { type: "input", placeholder: `Union of ${types}.` }
    // TODO: It's not clear what the inner type is
    case "list":
      return { type: "array" }
    case "tuple":
      return { type: "array" }
    case "enum":
      return {
        type: "select",
        options: param.args.map((arg) => String(arg.value)),
      }
    case "dict":
      return { type: "json" }
    default:
      return { type: "input" }
  }
}

export function getPlatform(integrationKey: string): IntegrationPlatform {
  return integrationKey.split(".")[1] as IntegrationPlatform
}
