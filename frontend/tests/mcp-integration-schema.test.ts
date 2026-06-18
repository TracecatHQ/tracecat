import type { MCPConnectionSpec } from "@/client"
import {
  mcpIntegrationFormSchema,
  missingRequiredOAuthClientCredentials,
} from "@/components/integrations/mcp-integration-schema"

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

describe("SentinelOne Purple MCP validation", () => {
  function sentinelOneValues(stdioEnv: string) {
    return {
      name: "SentinelOne Purple",
      description: "",
      server_type: "stdio" as const,
      server_uri: "",
      auth_type: "CUSTOM" as const,
      oauth_setup: "mcp_discovery" as const,
      oauth_integration_id: "",
      oauth_client_credentials: "",
      custom_credentials: "",
      stdio_command: "uvx",
      stdio_args: [
        { value: "--from" },
        { value: "git+https://github.com/Sentinel-One/purple-mcp.git" },
        { value: "purple-mcp" },
        { value: "--mode" },
        { value: "stdio" },
      ],
      stdio_env: stdioEnv,
      required_stdio_env_keys: [
        "PURPLEMCP_CONSOLE_TOKEN",
        "PURPLEMCP_CONSOLE_BASE_URL",
      ],
      timeout: 30,
      catalog_slug: "sentinelone-mcp",
      connection_option_id: "",
    }
  }

  it("rejects an empty SentinelOne token", () => {
    const result = mcpIntegrationFormSchema.safeParse(
      sentinelOneValues(
        JSON.stringify({
          PURPLEMCP_CONSOLE_TOKEN: "",
          PURPLEMCP_CONSOLE_BASE_URL: "https://acme.sentinelone.net",
        })
      )
    )

    expect(result.success).toBe(false)
    if (result.success) {
      throw new Error("Expected SentinelOne validation to fail")
    }
    expect(result.error.issues.map((issue) => issue.message)).toContain(
      "Required environment variables must be present with non-empty values"
    )
  })

  it("rejects missing required SentinelOne env keys", () => {
    const result = mcpIntegrationFormSchema.safeParse(
      sentinelOneValues(
        JSON.stringify({
          PURPLEMCP_CONSOLE_BASE_URL: "https://acme.sentinelone.net",
        })
      )
    )

    expect(result.success).toBe(false)
    if (result.success) {
      throw new Error("Expected SentinelOne validation to fail")
    }
    expect(result.error.issues.map((issue) => issue.message)).toContain(
      "Required environment variables must be present with non-empty values"
    )
  })

  it("rejects SentinelOne console base URLs with paths", () => {
    const result = mcpIntegrationFormSchema.safeParse(
      sentinelOneValues(
        JSON.stringify({
          PURPLEMCP_CONSOLE_TOKEN: "token123",
          PURPLEMCP_CONSOLE_BASE_URL:
            "https://acme.sentinelone.net/web/api/v2.1",
        })
      )
    )

    expect(result.success).toBe(false)
    if (result.success) {
      throw new Error("Expected SentinelOne validation to fail")
    }
    expect(result.error.issues.map((issue) => issue.message)).toContain(
      "SentinelOne console base URL must be an HTTPS origin like https://your-console.sentinelone.net"
    )
  })

  it("accepts an HTTPS SentinelOne console origin", () => {
    const result = mcpIntegrationFormSchema.safeParse(
      sentinelOneValues(
        JSON.stringify({
          PURPLEMCP_CONSOLE_TOKEN: "token123",
          PURPLEMCP_CONSOLE_BASE_URL: "https://acme.sentinelone.net",
        })
      )
    )

    expect(result.success).toBe(true)
  })
})
