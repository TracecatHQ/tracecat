import { client } from "@/lib/api"
import type {
  CaseDurationCreate,
  CaseDurationRead,
} from "@/types/case-durations"

export async function listCaseDurations(
  workspaceId: string
): Promise<CaseDurationRead[]> {
  const response = await client.get<CaseDurationRead[]>("/case-durations", {
    params: { workspace_id: workspaceId },
  })

  return response.data
}

export async function createCaseDuration(
  workspaceId: string,
  payload: CaseDurationCreate
): Promise<CaseDurationRead> {
  const response = await client.post<CaseDurationRead>(
    "/case-durations",
    payload,
    { params: { workspace_id: workspaceId } }
  )

  return response.data
}

export async function deleteCaseDuration(
  workspaceId: string,
  durationId: string
): Promise<void> {
  await client.delete(`/case-durations/${durationId}`, {
    params: { workspace_id: workspaceId },
  })
}
