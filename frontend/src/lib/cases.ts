import { Session } from "@supabase/supabase-js"
import { z } from "zod"

import { caseSchema, type Case } from "@/types/schemas"
import { getAuthenticatedClient } from "@/lib/api"

export async function getCases(
  session: Session | null,
  workflowId: string
): Promise<Case[]> {
  try {
    const client = getAuthenticatedClient(session)
    const response = await client.get<Case[]>(`/workflows/${workflowId}/cases`)
    return z.array(caseSchema).parse(response.data)
  } catch (error) {
    console.error("Error fetching cases:", error)
    throw error
  }
}
