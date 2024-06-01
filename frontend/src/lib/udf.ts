import { useQuery } from "@tanstack/react-query"
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
export function useUDFSchema(
  key: string,
  namespace?: string
): {
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

  if (!udf) {
    return {
      udf,
      isLoading,
    }
  }
  return { udf, isLoading }
}
