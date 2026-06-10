import type { MCPConnectionSpec } from "@/client"
import { missingRequiredOAuthClientCredentials } from "@/components/integrations/mcp-integration-schema"

function oauthSpec(
  credentials: NonNullable<MCPConnectionSpec["credentials"]>
): MCPConnectionSpec {
  return {
    server_type: "http",
    auth_type: "OAUTH2",
    server_uri: "https://mcp.example.com/mcp",
    config_fields: [],
    credentials,
  }
}

describe("missingRequiredOAuthClientCredentials", () => {
  const spec = oauthSpec([
    {
      key: "client_id",
      label: "Client ID",
      description: "OAuth client ID",
      required: true,
      secret: false,
      target: "oauth_client",
    },
    {
      key: "client_secret",
      label: "Client secret",
      description: "OAuth client secret",
      required: true,
      secret: true,
      target: "oauth_client",
    },
  ])

  it("flags required credentials left empty in the prefilled template", () => {
    const template = JSON.stringify({ client_id: "", client_secret: "" })
    expect(missingRequiredOAuthClientCredentials(spec, template)).toEqual([
      "Client ID",
      "Client secret",
    ])
  })

  it("flags a required client secret when only the client ID is filled", () => {
    const value = JSON.stringify({ client_id: "abc123", client_secret: "  " })
    expect(missingRequiredOAuthClientCredentials(spec, value)).toEqual([
      "Client secret",
    ])
  })

  it("returns nothing when all required credentials are filled", () => {
    const value = JSON.stringify({
      client_id: "abc123",
      client_secret: "shh",
    })
    expect(missingRequiredOAuthClientCredentials(spec, value)).toEqual([])
  })

  it("matches keys leniently across formatting differences", () => {
    const value = JSON.stringify({
      "Client-ID": "abc123",
      CLIENT_SECRET: "shh",
    })
    expect(missingRequiredOAuthClientCredentials(spec, value)).toEqual([])
  })

  it("ignores optional credentials and non-oauth_client targets", () => {
    const mixedSpec = oauthSpec([
      {
        key: "client_id",
        label: "Client ID",
        description: "OAuth client ID",
        required: true,
        secret: false,
        target: "oauth_client",
      },
      {
        key: "client_secret",
        label: "Client secret",
        description: "OAuth client secret",
        required: false,
        secret: true,
        target: "oauth_client",
      },
      {
        key: "api_key",
        label: "API key",
        description: "Header credential",
        required: true,
        secret: true,
        target: "http_header",
      },
    ])
    const value = JSON.stringify({ client_id: "abc123", client_secret: "" })
    expect(missingRequiredOAuthClientCredentials(mixedSpec, value)).toEqual([])
  })

  it("treats unparseable JSON as having no missing credentials", () => {
    expect(missingRequiredOAuthClientCredentials(spec, "not json")).toEqual([])
  })
})
