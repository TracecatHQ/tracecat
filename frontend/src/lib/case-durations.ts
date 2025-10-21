import {
  type CaseDurationDefinitionCreate,
  type CaseDurationDefinitionRead,
  type CaseDurationDefinitionUpdate,
  type CaseDurationRead,
  caseDurationsCreateCaseDurationDefinition,
  caseDurationsDeleteCaseDurationDefinition,
  caseDurationsGetCaseDurationDefinition,
  caseDurationsListCaseDurationDefinitions,
  caseDurationsListCaseDurations,
  caseDurationsUpdateCaseDurationDefinition,
} from "@/client"

export type {
  CaseDurationDefinitionRead,
  CaseDurationDefinitionCreate,
  CaseDurationDefinitionUpdate,
  CaseDurationRead,
}

export async function listCaseDurationDefinitions(
  workspaceId: string
): Promise<CaseDurationDefinitionRead[]> {
  return await caseDurationsListCaseDurationDefinitions({ workspaceId })
}

export async function getCaseDurationDefinition(
  workspaceId: string,
  durationId: string
): Promise<CaseDurationDefinitionRead> {
  return await caseDurationsGetCaseDurationDefinition({
    workspaceId,
    durationId,
  })
}

export async function createCaseDurationDefinition(
  workspaceId: string,
  requestBody: CaseDurationDefinitionCreate
): Promise<CaseDurationDefinitionRead> {
  return await caseDurationsCreateCaseDurationDefinition({
    workspaceId,
    requestBody,
  })
}

export async function updateCaseDurationDefinition(
  workspaceId: string,
  durationId: string,
  requestBody: CaseDurationDefinitionUpdate
): Promise<CaseDurationDefinitionRead> {
  return await caseDurationsUpdateCaseDurationDefinition({
    workspaceId,
    durationId,
    requestBody,
  })
}

export async function deleteCaseDurationDefinition(
  workspaceId: string,
  durationId: string
): Promise<void> {
  await caseDurationsDeleteCaseDurationDefinition({ workspaceId, durationId })
}

export async function listCaseDurations(
  workspaceId: string,
  caseId: string
): Promise<CaseDurationRead[]> {
  return await caseDurationsListCaseDurations({ workspaceId, caseId })
}
