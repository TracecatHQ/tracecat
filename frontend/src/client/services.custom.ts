import type { CancelablePromise } from "./core/CancelablePromise"
import { OpenAPI } from "./core/OpenAPI"
import { request } from "./core/request"
import type {
  CaseEventType,
  OAuthGrantType,
  OrganizationTierRead,
  ProviderReadMinimal,
} from "./types.gen"

export type CustomOAuthProviderCreateRequest = {
  name: string
  description?: string | null
  grant_type: OAuthGrantType
  authorization_endpoint: string
  token_endpoint: string
  scopes?: string[] | null
  provider_id?: string | null
  client_id: string
  client_secret?: string | null
}

export type ProvidersCreateCustomProviderData = {
  workspaceId: string
  requestBody: CustomOAuthProviderCreateRequest
}

export const providersCreateCustomProvider = (
  data: ProvidersCreateCustomProviderData
): CancelablePromise<ProviderReadMinimal> =>
  request(OpenAPI, {
    method: "POST",
    url: "/providers",
    query: {
      workspace_id: data.workspaceId,
    },
    body: data.requestBody,
    mediaType: "application/json",
    errors: {
      422: "Validation Error",
    },
  })

export type CaseTriggerStatus = "online" | "offline"

export type CaseTriggerRead = {
  id: string
  workflow_id: string
  status: CaseTriggerStatus
  event_types: CaseEventType[]
  tag_filters: string[]
}

export type CaseTriggerCreate = {
  status: CaseTriggerStatus
  event_types: CaseEventType[]
  tag_filters: string[]
}

export type CaseTriggerUpdate = Partial<CaseTriggerCreate>

export type TriggersGetCaseTriggerData = {
  workspaceId: string
  workflowId: string
}

export type TriggersCreateCaseTriggerData = {
  workspaceId: string
  workflowId: string
  requestBody: CaseTriggerCreate
}

export type TriggersUpdateCaseTriggerData = {
  workspaceId: string
  workflowId: string
  requestBody: CaseTriggerUpdate
}

export const triggersGetCaseTrigger = (
  data: TriggersGetCaseTriggerData
): CancelablePromise<CaseTriggerRead> =>
  request(OpenAPI, {
    method: "GET",
    url: "/workflows/{workflow_id}/case-trigger",
    path: {
      workflow_id: data.workflowId,
    },
    query: {
      workspace_id: data.workspaceId,
    },
    errors: {
      404: "Not Found",
      422: "Validation Error",
    },
  })

export const triggersCreateCaseTrigger = (
  data: TriggersCreateCaseTriggerData
): CancelablePromise<CaseTriggerRead> =>
  request(OpenAPI, {
    method: "POST",
    url: "/workflows/{workflow_id}/case-trigger",
    path: {
      workflow_id: data.workflowId,
    },
    query: {
      workspace_id: data.workspaceId,
    },
    body: data.requestBody,
    mediaType: "application/json",
    errors: {
      404: "Not Found",
      422: "Validation Error",
    },
  })

export const triggersUpdateCaseTrigger = (
  data: TriggersUpdateCaseTriggerData
): CancelablePromise<void> =>
  request(OpenAPI, {
    method: "PATCH",
    url: "/workflows/{workflow_id}/case-trigger",
    path: {
      workflow_id: data.workflowId,
    },
    query: {
      workspace_id: data.workspaceId,
    },
    body: data.requestBody,
    mediaType: "application/json",
    errors: {
      404: "Not Found",
      422: "Validation Error",
    },
  })

export type AdminListOrgTiersData = {
  orgIds?: string[]
}

export type AdminDeleteOrganizationWithConfirmationData = {
  orgId: string
  confirm: string
}

export const adminDeleteOrganizationWithConfirmation = (
  data: AdminDeleteOrganizationWithConfirmationData
): CancelablePromise<void> =>
  request(OpenAPI, {
    method: "DELETE",
    url: "/admin/organizations/{org_id}",
    path: {
      org_id: data.orgId,
    },
    query: {
      confirm: data.confirm,
    },
    errors: {
      400: "Bad Request",
      404: "Not Found",
      422: "Validation Error",
    },
  })

export const adminListOrgTiers = (
  data: AdminListOrgTiersData
): CancelablePromise<OrganizationTierRead[]> =>
  request(OpenAPI, {
    method: "GET",
    url: "/admin/tiers/organizations",
    query: {
      org_ids: data.orgIds,
    },
    errors: {
      422: "Validation Error",
    },
  })

export type OrgScheduleTemporalStatus = "present" | "missing"
export type OrgScheduleDbStatus = "online" | "offline"

export type OrgScheduleTemporalItem = {
  schedule_id: string
  workspace_id: string
  workspace_name: string
  workflow_id: string | null
  workflow_title: string | null
  db_status: OrgScheduleDbStatus
  temporal_status: OrgScheduleTemporalStatus
  last_checked_at: string
  error?: string | null
}

export type OrgScheduleTemporalSummary = {
  total_schedules: number
  present_count: number
  missing_count: number
}

export type OrgScheduleTemporalSyncRead = {
  summary: OrgScheduleTemporalSummary
  items: OrgScheduleTemporalItem[]
}

export type OrgScheduleRecreateAction = "created" | "skipped_present" | "failed"

export type OrgScheduleRecreateResult = {
  schedule_id: string
  action: OrgScheduleRecreateAction
  error?: string | null
}

export type OrgScheduleRecreateResponse = {
  processed_count: number
  created_count: number
  already_present_count: number
  failed_count: number
  results: OrgScheduleRecreateResult[]
}

export type OrganizationRecreateMissingTemporalSchedulesData = {
  requestBody: {
    schedule_ids?: string[] | null
  }
}

export const organizationGetScheduleTemporalSync =
  (): CancelablePromise<OrgScheduleTemporalSyncRead> =>
    request(OpenAPI, {
      method: "GET",
      url: "/organization/schedules/temporal-sync",
      errors: {
        403: "Forbidden",
        422: "Validation Error",
      },
    })

export const organizationRecreateMissingTemporalSchedules = (
  data: OrganizationRecreateMissingTemporalSchedulesData
): CancelablePromise<OrgScheduleRecreateResponse> =>
  request(OpenAPI, {
    method: "POST",
    url: "/organization/schedules/temporal-sync/recreate-missing",
    body: data.requestBody,
    mediaType: "application/json",
    errors: {
      403: "Forbidden",
      422: "Validation Error",
    },
  })
