import { useSession } from "@/providers/session"
import { Session } from "@supabase/supabase-js"
import { useQuery } from "@tanstack/react-query"
import { z } from "zod"

import { Integration, integrationSchema } from "@/types/schemas"
import { getAuthenticatedClient } from "@/lib/api"

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
