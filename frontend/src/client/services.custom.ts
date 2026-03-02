import type { CancelablePromise } from "./core/CancelablePromise"
import { OpenAPI } from "./core/OpenAPI"
import { request } from "./core/request"
import type {
  CaseEventType,
  OAuthGrantType,
  OrganizationTierRead,
  ProviderReadMinimal,
  TriggerType,
  WorkflowExecutionReadMinimal,
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

export type WorkflowExecutionRelationFilter = "all" | "root" | "child"
export type WorkflowExecutionStatusFilterMode = "include" | "exclude"
export type WorkflowExecutionStatusFilter =
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELED"
  | "TERMINATED"
  | "CONTINUED_AS_NEW"
  | "TIMED_OUT"

export type WorkflowExecutionResetReapplyType =
  | "all_eligible"
  | "signal_only"
  | "none"

export type WorkflowRunReadMinimal = WorkflowExecutionReadMinimal & {
  workflow_id?: string | null
  workflow_title?: string | null
  workflow_alias?: string | null
}

export type CursorPaginatedWorkflowRunsResponse = {
  items: WorkflowRunReadMinimal[]
  next_cursor: string | null
  prev_cursor: string | null
  has_more: boolean
  has_previous: boolean
  total_estimate: number | null
}

export type WorkflowExecutionResetPointRead = {
  event_id: number
  event_time: string
  event_type: string
  label: string
  is_start: boolean
  is_resettable: boolean
}

export type WorkflowExecutionResetRequest = {
  event_id?: number | null
  reason?: string | null
  reapply_type?: WorkflowExecutionResetReapplyType
}

export type WorkflowExecutionResetResponse = {
  execution_id: string
  new_run_id: string
}

export type WorkflowExecutionBulkResetRequest = {
  execution_ids: string[]
  event_id?: number | null
  reason?: string | null
  reapply_type?: WorkflowExecutionResetReapplyType
}

export type WorkflowExecutionBulkResetItemResult = {
  execution_id: string
  ok: boolean
  new_run_id?: string | null
  error?: string | null
}

export type WorkflowExecutionBulkResetResponse = {
  results: WorkflowExecutionBulkResetItemResult[]
}

export type WorkflowExecutionsSearchWorkflowExecutionsData = {
  workspaceId: string
  limit?: number
  cursor?: string | null
  workflowId?: string | null
  trigger?: TriggerType[] | null
  userId?: string | null
  status?: WorkflowExecutionStatusFilter[] | null
  statusMode?: WorkflowExecutionStatusFilterMode
  startTimeFrom?: string | null
  startTimeTo?: string | null
  closeTimeFrom?: string | null
  closeTimeTo?: string | null
  durationGteSeconds?: number | null
  durationLteSeconds?: number | null
  searchTerm?: string | null
  relation?: WorkflowExecutionRelationFilter
}

export const workflowExecutionsSearchWorkflowExecutions = (
  data: WorkflowExecutionsSearchWorkflowExecutionsData
): CancelablePromise<CursorPaginatedWorkflowRunsResponse> =>
  request(OpenAPI, {
    method: "GET",
    url: "/workflow-executions/search",
    query: {
      workspace_id: data.workspaceId,
      limit: data.limit,
      cursor: data.cursor,
      workflow_id: data.workflowId,
      trigger: data.trigger,
      user_id: data.userId,
      status: data.status,
      status_mode: data.statusMode,
      start_time_from: data.startTimeFrom,
      start_time_to: data.startTimeTo,
      close_time_from: data.closeTimeFrom,
      close_time_to: data.closeTimeTo,
      duration_gte_seconds: data.durationGteSeconds,
      duration_lte_seconds: data.durationLteSeconds,
      search_term: data.searchTerm,
      relation: data.relation,
    },
    errors: {
      400: "Bad Request",
      422: "Validation Error",
    },
  })

export type WorkflowExecutionsListResetPointsData = {
  workspaceId: string
  executionId: string
  limit?: number
}

export const workflowExecutionsListResetPoints = (
  data: WorkflowExecutionsListResetPointsData
): CancelablePromise<WorkflowExecutionResetPointRead[]> =>
  request(OpenAPI, {
    method: "GET",
    url: "/workflow-executions/{execution_id}/reset-points",
    path: {
      execution_id: data.executionId,
    },
    query: {
      workspace_id: data.workspaceId,
      limit: data.limit,
    },
    errors: {
      404: "Not Found",
      422: "Validation Error",
    },
  })

export type WorkflowExecutionsResetWorkflowExecutionData = {
  workspaceId: string
  executionId: string
  requestBody: WorkflowExecutionResetRequest
}

export const workflowExecutionsResetWorkflowExecution = (
  data: WorkflowExecutionsResetWorkflowExecutionData
): CancelablePromise<WorkflowExecutionResetResponse> =>
  request(OpenAPI, {
    method: "POST",
    url: "/workflow-executions/{execution_id}/reset",
    path: {
      execution_id: data.executionId,
    },
    query: {
      workspace_id: data.workspaceId,
    },
    body: data.requestBody,
    mediaType: "application/json",
    errors: {
      400: "Bad Request",
      404: "Not Found",
      422: "Validation Error",
    },
  })

export type WorkflowExecutionsBulkResetWorkflowExecutionsData = {
  workspaceId: string
  requestBody: WorkflowExecutionBulkResetRequest
}

export const workflowExecutionsBulkResetWorkflowExecutions = (
  data: WorkflowExecutionsBulkResetWorkflowExecutionsData
): CancelablePromise<WorkflowExecutionBulkResetResponse> =>
  request(OpenAPI, {
    method: "POST",
    url: "/workflow-executions/reset/bulk",
    query: {
      workspace_id: data.workspaceId,
    },
    body: data.requestBody,
    mediaType: "application/json",
    errors: {
      422: "Validation Error",
    },
  })
