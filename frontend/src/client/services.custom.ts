import type { CancelablePromise } from "./core/CancelablePromise"
import { OpenAPI } from "./core/OpenAPI"
import { request } from "./core/request"
import type { OAuthGrantType, ProviderReadMinimal } from "./types.gen"

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
