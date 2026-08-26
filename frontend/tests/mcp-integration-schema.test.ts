import type { MCPConnectionSpec } from "@/client"
import {
  buildMcpIntegrationFormSchema,
  catalogEntryToFormValues,
  invalidUrlEnvKeys,
  MCP_INTEGRATION_FORM_DEFAULTS,
  missingRequiredOAuthClientCredentials,
  urlTypedStdioEnvKeys,
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

function stdioSpec(
  credentials: NonNullable<MCPConnectionSpec["credentials"]>
): MCPConnectionSpec {
  return {
    server_type: "stdio",
    stdio_command: "npx",
    stdio_args: [],
    config_fields: [],
    credentials,
  } as unknown as MCPConnectionSpec
}

describe("urlTypedStdioEnvKeys", () => {
  it("collects only stdio_env credentials marked type: url", () => {
    const spec = stdioSpec([
      {
        key: "VAULT_ADDR",
        label: "Vault Server Address",
        description: "Base URL",
        required: true,
        secret: false,
        type: "url",
        target: "stdio_env",
      },
      {
        key: "VAULT_TOKEN",
        label: "Vault Token",
        description: "Auth token",
        required: true,
        secret: true,
        target: "stdio_env",
      },
    ])
    expect(urlTypedStdioEnvKeys(spec)).toEqual(new Set(["VAULT_ADDR"]))
  })

  it("ignores url-typed credentials on non-stdio_env targets", () => {
    const spec = stdioSpec([
      {
        key: "SERVER_URL",
        label: "Server URL",
        description: "Base URL",
        required: true,
        secret: false,
        type: "url",
        target: "http_header",
      },
    ])
    expect(urlTypedStdioEnvKeys(spec)).toEqual(new Set())
  })

  it("returns an empty set for a null spec", () => {
    expect(urlTypedStdioEnvKeys(null)).toEqual(new Set())
  })
})

describe("invalidUrlEnvKeys", () => {
  const urlKeys = new Set(["VAULT_ADDR"])

  it("flags a scheme-less value", () => {
    const value = JSON.stringify({ VAULT_ADDR: "acme.example.net" })
    expect(invalidUrlEnvKeys(value, urlKeys)).toEqual(["VAULT_ADDR"])
  })

  it("accepts an http(s):// value", () => {
    const value = JSON.stringify({ VAULT_ADDR: "https://vault.example.net" })
    expect(invalidUrlEnvKeys(value, urlKeys)).toEqual([])
  })

  it("skips empty and templated values", () => {
    expect(
      invalidUrlEnvKeys(JSON.stringify({ VAULT_ADDR: "" }), urlKeys)
    ).toEqual([])
    expect(
      invalidUrlEnvKeys(
        JSON.stringify({ VAULT_ADDR: "${{ SECRETS.vault.ADDR }}" }),
        urlKeys
      )
    ).toEqual([])
  })

  it("ignores keys outside urlKeys", () => {
    const value = JSON.stringify({ VAULT_TOKEN: "not-a-url" })
    expect(invalidUrlEnvKeys(value, urlKeys)).toEqual([])
  })

  it("returns [] when there are no url keys or the JSON is unparseable", () => {
    expect(invalidUrlEnvKeys("not json", urlKeys)).toEqual([])
    expect(
      invalidUrlEnvKeys(JSON.stringify({ VAULT_ADDR: "x" }), new Set())
    ).toEqual([])
  })
})

describe("buildMcpIntegrationFormSchema url validation", () => {
  const baseStdio = {
    ...MCP_INTEGRATION_FORM_DEFAULTS,
    name: "Vault",
    server_type: "stdio" as const,
    stdio_command: "npx",
  }
  const urlEnvKeys = new Set(["VAULT_ADDR"])

  it("rejects a scheme-less url-typed stdio env var", () => {
    const result = buildMcpIntegrationFormSchema(urlEnvKeys).safeParse({
      ...baseStdio,
      stdio_env: JSON.stringify({ VAULT_ADDR: "acme.example.net" }),
    })
    expect(result.success).toBe(false)
    if (!result.success) {
      const issue = result.error.issues.find((i) => i.path[0] === "stdio_env")
      expect(issue?.message).toContain("VAULT_ADDR")
    }
  })

  it("accepts a well-formed url-typed stdio env var", () => {
    const result = buildMcpIntegrationFormSchema(urlEnvKeys).safeParse({
      ...baseStdio,
      stdio_env: JSON.stringify({ VAULT_ADDR: "https://vault.example.net" }),
    })
    expect(result.success).toBe(true)
  })

  it("does not validate url shape when the spec declares no url keys", () => {
    const result = buildMcpIntegrationFormSchema(new Set()).safeParse({
      ...baseStdio,
      stdio_env: JSON.stringify({ VAULT_ADDR: "acme.example.net" }),
    })
    expect(result.success).toBe(true)
  })
})

describe("catalogEntryToFormValues", () => {
  it("prefills default header credential values", () => {
    const values = catalogEntryToFormValues({
      slug: "wiz-mcp",
      name: "Wiz",
      description: "Investigate cloud security findings on Wiz",
      connection_spec: {
        server_type: "http",
        auth_type: "CUSTOM",
        server_uri: "https://mcp.app.wiz.io",
        config_fields: [],
        credentials: [
          {
            key: "Wiz-Client-Id",
            label: "Wiz Client ID",
            description: "Client ID",
            required: true,
            secret: false,
            target: "http_header",
          },
          {
            key: "X-Wiz-MCP-Mode",
            label: "Wiz MCP Mode",
            description: "Gateway mode reduces token usage",
            required: true,
            secret: false,
            default_value: "gateway",
            target: "http_header",
          },
        ],
      },
    } as unknown as Parameters<typeof catalogEntryToFormValues>[0])

    expect(JSON.parse(values.custom_credentials ?? "{}")).toEqual({
      "Wiz-Client-Id": "",
      "X-Wiz-MCP-Mode": "gateway",
    })
  })
})
