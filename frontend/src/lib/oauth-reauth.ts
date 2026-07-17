import type { IntegrationConnectOverrides, IntegrationRead } from "@/client"

/**
 * Handshake fields that, when changed on a connected authorization_code
 * integration, require a full OAuth reauthorization rather than an eager PUT.
 */
export type HandshakeField =
  | "client_id"
  | "client_secret"
  | "authorization_endpoint"
  | "token_endpoint"
  | "scopes"

/**
 * Normalized form values relevant to the OAuth handshake. `clientSecret` is the
 * raw value entered in the form (empty means "keep existing").
 */
export interface HandshakeFormValues {
  clientId: string
  clientSecret: string
  authorizationEndpoint: string
  tokenEndpoint: string
  scopes: string[]
}

function normalizeScopes(
  scopes: readonly string[] | null | undefined
): string[] {
  return [...(scopes ?? [])]
    .map((scope) => scope.trim())
    .filter((scope) => scope.length > 0)
    .sort()
}

function scopesDiffer(
  a: readonly string[] | null | undefined,
  b: readonly string[] | null | undefined
): boolean {
  const left = normalizeScopes(a)
  const right = normalizeScopes(b)
  if (left.length !== right.length) {
    return true
  }
  return left.some((value, index) => value !== right[index])
}

/**
 * Compute which OAuth handshake fields the submitted form values change relative
 * to the live integration.
 *
 * Rules:
 * - `client_id`, `authorization_endpoint`, `token_endpoint`: changed when the
 *   trimmed submitted value differs from the integration's stored value.
 * - `client_secret`: changed whenever a non-empty secret is entered (secrets are
 *   write-only, so any submitted value is treated as a rotation).
 * - `scopes`: changed when the submitted set differs from
 *   `integration.requested_scopes` (order-insensitive).
 */
export function computeChangedHandshakeFields(
  values: HandshakeFormValues,
  integration: IntegrationRead
): HandshakeField[] {
  const changed: HandshakeField[] = []

  if (values.clientId.trim() !== (integration.client_id ?? "").trim()) {
    changed.push("client_id")
  }
  if (values.clientSecret.trim().length > 0) {
    changed.push("client_secret")
  }
  if (
    values.authorizationEndpoint.trim() !==
    (integration.authorization_endpoint ?? "").trim()
  ) {
    changed.push("authorization_endpoint")
  }
  if (
    values.tokenEndpoint.trim() !== (integration.token_endpoint ?? "").trim()
  ) {
    changed.push("token_endpoint")
  }
  if (scopesDiffer(values.scopes, integration.requested_scopes)) {
    changed.push("scopes")
  }

  return changed
}

/**
 * Build the {@link IntegrationConnectOverrides} payload for a reauthorization,
 * including only the fields listed in `changedFields`.
 */
export function buildReauthOverrides(
  values: HandshakeFormValues,
  changedFields: readonly HandshakeField[]
): IntegrationConnectOverrides {
  const overrides: IntegrationConnectOverrides = {}
  for (const field of changedFields) {
    switch (field) {
      case "client_id":
        overrides.client_id = values.clientId.trim() || null
        break
      case "client_secret":
        overrides.client_secret = values.clientSecret.trim()
        break
      case "authorization_endpoint":
        overrides.authorization_endpoint = values.authorizationEndpoint.trim()
        break
      case "token_endpoint":
        overrides.token_endpoint = values.tokenEndpoint.trim()
        break
      case "scopes":
        overrides.scopes = normalizeScopes(values.scopes)
        break
      default: {
        const _exhaustive: never = field
        return _exhaustive
      }
    }
  }
  return overrides
}
