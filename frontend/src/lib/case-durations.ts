import {
  type CaseDurationCreate,
  type CaseDurationRead,
  type CaseDurationUpdate,
  caseDurationsCreateCaseDuration,
  caseDurationsDeleteCaseDuration,
  caseDurationsGetCaseDuration,
  caseDurationsListCaseDurations,
  caseDurationsUpdateCaseDuration,
} from "@/client"

export type { CaseDurationRead, CaseDurationCreate, CaseDurationUpdate }

export async function listCaseDurations(
  workspaceId: string
): Promise<CaseDurationRead[]> {
  return await caseDurationsListCaseDurations({ workspaceId })
}

export async function getCaseDuration(
  workspaceId: string,
  durationId: string
): Promise<CaseDurationRead> {
  return await caseDurationsGetCaseDuration({ workspaceId, durationId })
}

export async function createCaseDuration(
  workspaceId: string,
  requestBody: CaseDurationCreate
): Promise<CaseDurationRead> {
  return await caseDurationsCreateCaseDuration({ workspaceId, requestBody })
}

export async function updateCaseDuration(
  workspaceId: string,
  durationId: string,
  requestBody: CaseDurationUpdate
): Promise<CaseDurationRead> {
  return await caseDurationsUpdateCaseDuration({
    workspaceId,
    durationId,
    requestBody,
  })
}

export async function deleteCaseDuration(
  workspaceId: string,
  durationId: string
): Promise<void> {
  await caseDurationsDeleteCaseDuration({ workspaceId, durationId })
}
