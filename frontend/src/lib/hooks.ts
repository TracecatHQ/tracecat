import { useEffect, useState } from "react"
import { Session } from "@supabase/supabase-js"
import { useQuery } from "@tanstack/react-query"
import { z } from "zod"

import { Integration, IntegrationType } from "@/types/schemas"
import { fetchIntegration, parseSpec } from "@/lib/integrations"
import {
  ActionFieldConfig,
  baseActionSchema,
} from "@/components/workspace/panel/action/schemas"

export function useLocalStorage<T>(
  key: string,
  defaultValue: T
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") {
      return defaultValue
    }
    const storedValue = localStorage.getItem(key)
    return storedValue ? JSON.parse(storedValue) : defaultValue
  })
  useEffect(() => {
    localStorage.setItem(key, JSON.stringify(value))
  }, [key, value])
  return [value, setValue]
}

export function useIntegrationFormSchema(
  session: Session | null,
  integrationKey: IntegrationType
): {
  isLoading: boolean
  fieldSchema: z.ZodObject<Record<string, any>>
  fieldConfig: ActionFieldConfig
  integrationSpec?: Integration
} {
  const { data: integrationSpec, isLoading } = useQuery({
    queryKey: ["integration_field_config", integrationKey],
    queryFn: async ({ queryKey }) => {
      const [_, integrationKey] = queryKey as [string, IntegrationType]
      return await fetchIntegration(session, integrationKey)
    },
  })
  // Parse the schema and config
  if (!integrationSpec) {
    return {
      fieldSchema: baseActionSchema,
      fieldConfig: {},
      isLoading,
    }
  }
  const { fieldSchema, fieldConfig } = parseSpec(integrationSpec.parameters)

  return { fieldSchema, fieldConfig, isLoading, integrationSpec }
}
