import { ApiError, udfsGetUdf, udfsListUdfs, UDFSpec } from "@/client"
import { useQuery } from "@tanstack/react-query"
import { z } from "zod"

import { client } from "@/lib/api"

//////////////////////////////////////////
// UDF related
//////////////////////////////////////////
export const UDFMetadataSchema = z
  .object({
    display_group: z.string().nullish(),
    default_title: z.string().nullish(),
    include_in_schema: z.boolean(),
  })
  .passthrough()
export type UDFMetadata = z.infer<typeof UDFMetadataSchema>

export const UDFRegistrySecretSchema = z.object({
  name: z.string(),
  keys: z.array(z.string()),
})
export type UDFRegistrySecret = z.infer<typeof UDFRegistrySecretSchema>

export const UDFSchema = z.object({
  args: z.record(z.string(), z.unknown()),
  rtype: z.record(z.string(), z.unknown()),
  description: z.string(),
  key: z.string(),
  version: z.string().nullable(),
  metadata: UDFMetadataSchema.nullable(),
  namespace: z.string(),
  secrets: z.array(UDFRegistrySecretSchema).nullable(),
})

export type UDF = z.infer<typeof UDFSchema>

// export const UDFSpecSchema = z.object({
//   owner_id: z.string(),
//   created_at: strAsDate,
//   updated_at: strAsDate,
//   id: z.string(),
//   description: z.string(),
//   namespace: z.string(),
//   key: z.string(),
//   version: z.string().nullable(),
//   meta: z.record(z.string(), z.unknown()).nullable(),
//   json_schema: UDFSchema,
// })
// export type UDFSpec = z.infer<typeof UDFSpecSchema>

// export async function fetchAllUDFs(
//   namespaces: string[],
//   workspaceId: string
// ): Promise<UDF[]> {
//   try {
//     const udfResponses = await udfsListUdfs({ ns: namespaces, workspaceId })
//     return udfResponses.map((u) => u.json_schema)
//   } catch (e) {
//     console.error("Error parsing UDFs", e)
//     throw e
//   }
// }

// export async function fetchUDF(key: string, namespace?: string): Promise<UDF> {
//   let path = `/udfs/${key}`
//   if (namespace) {
//     path += `?namespace=${namespace}`
//   }
//   const response = await client.get<UDF>(path)
//   const udfspec = await UDFSpecSchema.parseAsync(response.data)
//   return udfspec.json_schema
// }

export function useUDFs(
  workspaceId: string,
  namespaces: string[]
): {
  udfs?: UDF[]
  isLoading: boolean
  error: Error | null
} {
  const {
    data: udfs,
    isLoading,
    error,
  } = useQuery<UDFSpec[], ApiError>({
    queryKey: ["udfs"],
    queryFn: async () => await udfsListUdfs({ ns: namespaces, workspaceId }),
  })

  try {
    const udfSchemas = udfs
      ?.filter((u) => Boolean(u.json_schema))
      .map((u) => UDFSchema.parse(u.json_schema))
    return { udfs: udfSchemas, isLoading, error }
  } catch (e) {
    console.error("Error parsing UDFs", e)
    return { udfs: [], isLoading: false, error: e as Error }
  }
}

export function useUDFSchema(
  key: string,
  workspaceId: string,
  namespace?: string
): {
  udf?: UDF
  isLoading: boolean
} {
  const { data: udf, isLoading } = useQuery({
    queryKey: ["udf_field_config", key],
    queryFn: async ({ queryKey }) => {
      return await udfsGetUdf({
        udfKey: queryKey[1] as string,
        namespace,
        workspaceId,
      })
    },
  })

  const udfSchema = udf?.json_schema
    ? UDFSchema.parse(udf.json_schema)
    : undefined
  return { udf: udfSchema, isLoading }
}

/**
 * This is mirrored from pydantic_core.ErrorDetails
 */
const ErrorDetailsSchema = z.object({
  type: z.string(),
  loc: z.array(z.union([z.string(), z.number()])),
  msg: z.string(),
  input: z.unknown(),
  ctx: z.record(z.string(), z.unknown()).nullish(),
})
export type ErrorDetails = z.infer<typeof ErrorDetailsSchema>

const UDFArgsValidationResponseSchema = z.object({
  ok: z.boolean(),
  message: z.string(),
  detail: z.array(ErrorDetailsSchema).nullable(),
})
export type UDFArgsValidationResponse = z.infer<
  typeof UDFArgsValidationResponseSchema
>

export async function validateUDFArgs(
  key: string,
  args: Record<string, unknown>
): Promise<UDFArgsValidationResponse> {
  const response = await client.post<UDFArgsValidationResponse>(
    `/udfs/${key}/validate`,
    args
  )
  try {
    return await UDFArgsValidationResponseSchema.parseAsync(response.data)
  } catch (e) {
    if (e instanceof z.ZodError) {
      console.error("Error parsing UDF validation response", e)
      console.error(e.issues)
      console.error(e.errors)
    }
    throw e
  }
}
