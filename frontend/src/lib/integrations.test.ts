import type { IntegrationReadMinimal, ProviderReadMinimal } from "@/client"
import { findProviderForIntegration } from "@/lib/integrations"

function provider(
  grantType: ProviderReadMinimal["grant_type"],
  name: string
): ProviderReadMinimal {
  return {
    id: "google_chronicle",
    name,
    description: `${name} provider`,
    requires_config: true,
    integration_status: "connected",
    enabled: true,
    grant_type: grantType,
  }
}

function integration(
  grantType: IntegrationReadMinimal["grant_type"]
): IntegrationReadMinimal {
  return {
    id: `integration-${grantType}`,
    provider_id: "google_chronicle",
    grant_type: grantType,
    status: "connected",
    is_expired: false,
  }
}

describe("findProviderForIntegration", () => {
  const providers = [
    provider("authorization_code", "Google Chronicle (User OAuth)"),
    provider("client_credentials", "Google Chronicle (Service account)"),
  ]

  it("resolves the service account variant for a client credentials integration", () => {
    expect(
      findProviderForIntegration(providers, integration("client_credentials"))
        ?.name
    ).toBe("Google Chronicle (Service account)")
  })

  it("resolves the user OAuth variant for an authorization code integration", () => {
    expect(
      findProviderForIntegration(providers, integration("authorization_code"))
        ?.name
    ).toBe("Google Chronicle (User OAuth)")
  })

  it("returns undefined when no provider matches the grant type", () => {
    expect(
      findProviderForIntegration(
        [provider("authorization_code", "Google Chronicle (User OAuth)")],
        integration("client_credentials")
      )
    ).toBeUndefined()
    expect(
      findProviderForIntegration(undefined, integration("client_credentials"))
    ).toBeUndefined()
  })
})
