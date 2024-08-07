import { z } from "zod"

import {
  CaseEvent,
  caseEventSchema,
  caseSchema,
  type Case,
} from "@/types/schemas"
import { client } from "@/lib/api"

export async function getCases(workflowId: string): Promise<Case[]> {
  try {
    const response = await client.get<Case[]>(`/workflows/${workflowId}/cases`)
    return z.array(caseSchema).parse(response.data)
  } catch (error) {
    console.error("Error fetching cases:", error)
    throw error
  }
}

export async function updateCases(
  workflowId: string, // They should all have the same workflow ID
  cases: Case[]
) {
  try {
    z.array(caseSchema).parse(cases)
    console.log("Updating cases", cases)

    const responses = await Promise.all(
      cases.map((c) => client.post(`/workflows/${workflowId}/cases/${c.id}`, c))
    )
    if (responses.some((r) => r.status !== 200)) {
      throw new Error("Failed to update cases")
    }
  } catch (error) {
    const err = error as Error
    console.error("Error updating cases:", error)
    console.error("Detail", err.cause, err.message, err.stack)
    throw error
  }
}

export async function fetchCase(
  workflowId: string,
  caseId: string
): Promise<Case> {
  try {
    const response = await client.get<Case>(
      `/workflows/${workflowId}/cases/${caseId}`
    )
    return caseSchema.parse(response.data)
  } catch (error) {
    console.error("Error fetching case:", error)
    throw error
  }
}

export async function updateCase(
  workflowId: string,
  caseId: string,
  case_: Case
) {
  try {
    const response = await client.post(
      `/workflows/${workflowId}/cases/${caseId}`,
      case_
    )
    if (response.status !== 200) {
      throw new Error("Failed to update case")
    }
  } catch (error) {
    console.error("Error updating case:", error)
    throw error
  }
}

export async function fetchCaseEvents(
  workflowId: string,
  caseId: string
): Promise<CaseEvent[]> {
  try {
    const response = await client.get<CaseEvent[]>(
      `/workflows/${workflowId}/cases/${caseId}/events`
    )
    return z.array(caseEventSchema).parse(response.data)
  } catch (error) {
    console.error("Error fetching case events:", error)
    throw error
  }
}

export type CaseEventParams = Omit<
  CaseEvent,
  "id" | "created_at" | "workflow_id" | "case_id" | "initiator_role"
>
export async function createCaseEvent(
  workflowId: string,
  caseId: string,
  payload: CaseEventParams
) {
  try {
    const response = await client.post(
      `/workflows/${workflowId}/cases/${caseId}/events`,
      payload
    )
    if (response.status !== 201) {
      throw new Error("Failed to create case event")
    }
  } catch (error) {
    console.error("Error creating case event:", error)
    throw error
  }
}
