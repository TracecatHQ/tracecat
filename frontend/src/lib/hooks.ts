import { useEffect, useState } from "react"
import { Session } from "@supabase/supabase-js"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { z } from "zod"

import { Integration, IntegrationType, type Case } from "@/types/schemas"
import { fetchCase, updateCase } from "@/lib/cases"
import { fetchIntegration, parseSpec } from "@/lib/integrations"
import { toast } from "@/components/ui/use-toast"
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

export function usePanelCase(
  session: Session | null,
  workflowId: string,
  caseId: string
) {
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery<Case, Error>({
    queryKey: ["case", caseId],
    queryFn: async () => await fetchCase(session, workflowId, caseId),
  })
  const { mutateAsync } = useMutation({
    mutationFn: (newCase: Case) =>
      updateCase(session, workflowId, caseId, newCase),
    onSuccess: () => {
      toast({
        title: "Updated case",
        description: "Your case has been updated successfully.",
      })
      queryClient.invalidateQueries({
        queryKey: ["case", caseId],
      })
      queryClient.invalidateQueries({
        queryKey: ["cases"],
      })
    },
    onError: (error) => {
      console.error("Failed to update action:", error)
      toast({
        title: "Failed to save action",
        description: "Could not update your action. Please try again.",
      })
    },
  })

  return {
    data,
    isLoading,
    error,
    mutateCaseAsync: mutateAsync,
  }
}
