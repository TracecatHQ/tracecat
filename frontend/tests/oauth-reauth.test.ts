import type { IntegrationRead } from "@/client"
import {
  buildReauthOverrides,
  computeChangedHandshakeFields,
  type HandshakeFormValues,
} from "@/lib/oauth-reauth"

const baseIntegration: IntegrationRead = {
  id: "integration-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  user_id: "user-1",
  provider_id: "slack",
  authorization_endpoint: "https://auth.example.com/authorize",
  token_endpoint: "https://auth.example.com/token",
  token_type: "Bearer",
  expires_at: null,
  client_id: "client-123",
  granted_scopes: ["read"],
  requested_scopes: ["read", "write"],
  status: "connected",
  is_expired: false,
}

function valuesFrom(
  overrides: Partial<HandshakeFormValues> = {}
): HandshakeFormValues {
  return {
    clientId: "client-123",
    clientSecret: "",
    authorizationEndpoint: "https://auth.example.com/authorize",
    tokenEndpoint: "https://auth.example.com/token",
    scopes: ["read", "write"],
    ...overrides,
  }
}

describe("computeChangedHandshakeFields", () => {
  it("returns no changed fields when values match the integration", () => {
    expect(
      computeChangedHandshakeFields(valuesFrom(), baseIntegration)
    ).toEqual([])
  })

  it("ignores scope order and surrounding whitespace", () => {
    const values = valuesFrom({
      scopes: [" write ", "read"],
      clientId: "  client-123 ",
    })
    expect(computeChangedHandshakeFields(values, baseIntegration)).toEqual([])
  })

  it("detects a changed client id", () => {
    const values = valuesFrom({ clientId: "client-456" })
    expect(computeChangedHandshakeFields(values, baseIntegration)).toEqual([
      "client_id",
    ])
  })

  it("treats any non-empty secret as a rotation", () => {
    const values = valuesFrom({ clientSecret: "  new-secret  " })
    expect(computeChangedHandshakeFields(values, baseIntegration)).toEqual([
      "client_secret",
    ])
  })

  it("detects changed endpoints and scopes", () => {
    const values = valuesFrom({
      authorizationEndpoint: "https://auth.example.com/authorize2",
      tokenEndpoint: "https://auth.example.com/token2",
      scopes: ["read"],
    })
    expect(computeChangedHandshakeFields(values, baseIntegration)).toEqual([
      "authorization_endpoint",
      "token_endpoint",
      "scopes",
    ])
  })
})

describe("buildReauthOverrides", () => {
  it("includes only the changed fields", () => {
    const values = valuesFrom({
      clientId: "client-456",
      clientSecret: "secret-789",
      scopes: ["read"],
    })
    const changed = computeChangedHandshakeFields(values, baseIntegration)
    expect(buildReauthOverrides(values, changed)).toEqual({
      client_id: "client-456",
      client_secret: "secret-789",
      scopes: ["read"],
    })
  })

  it("emits a null client id when cleared", () => {
    const values = valuesFrom({ clientId: "   " })
    expect(buildReauthOverrides(values, ["client_id"])).toEqual({
      client_id: null,
    })
  })

  it("normalizes and sorts scopes", () => {
    const values = valuesFrom({ scopes: [" write ", "read", ""] })
    expect(buildReauthOverrides(values, ["scopes"])).toEqual({
      scopes: ["read", "write"],
    })
  })
})
