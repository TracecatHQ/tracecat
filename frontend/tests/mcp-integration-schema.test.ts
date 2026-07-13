import type { MCPConnectionSpec } from "@/client"
import type { MCPStdioEnvRow } from "@/components/integrations/mcp-integration-schema"
import {
  buildMcpIntegrationFormSchema,
  buildStdioArgsLine,
  catalogEntryToFormValues,
  invalidUrlEnvKeys,
  MCP_INTEGRATION_FORM_DEFAULTS,
  missingRequiredOAuthClientCredentials,
  parseStdioArgsLine,
  shouldSendStdioFields,
  stdioEnvReadToRows,
  stdioEnvRowsToPreserveKeys,
  stdioEnvRowsToRecord,
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
    const value = [{ key: "VAULT_ADDR", value: "acme.example.net" }]
    expect(invalidUrlEnvKeys(value, urlKeys)).toEqual(["VAULT_ADDR"])
  })

  it("accepts an http(s):// value", () => {
    const value = [{ key: "VAULT_ADDR", value: "https://vault.example.net" }]
    expect(invalidUrlEnvKeys(value, urlKeys)).toEqual([])
  })

  it("skips empty and templated values", () => {
    expect(
      invalidUrlEnvKeys([{ key: "VAULT_ADDR", value: "" }], urlKeys)
    ).toEqual([])
    expect(
      invalidUrlEnvKeys(
        [{ key: "VAULT_ADDR", value: "${{ SECRETS.vault.ADDR }}" }],
        urlKeys
      )
    ).toEqual([])
  })

  it("skips hidden values", () => {
    expect(
      invalidUrlEnvKeys(
        [{ key: "VAULT_ADDR", value: "", value_hidden: true }],
        urlKeys
      )
    ).toEqual([])
  })

  it("ignores keys outside urlKeys", () => {
    const value = [{ key: "VAULT_TOKEN", value: "not-a-url" }]
    expect(invalidUrlEnvKeys(value, urlKeys)).toEqual([])
  })

  it("returns [] when there are no url keys", () => {
    expect(
      invalidUrlEnvKeys([{ key: "VAULT_ADDR", value: "x" }], new Set())
    ).toEqual([])
  })
})

describe("stdio args line helpers", () => {
  it("joins args into the editable line", () => {
    expect(buildStdioArgsLine(["--yes", "@example/server"])).toBe(
      "--yes @example/server"
    )
  })

  it("splits args on simple whitespace without shell quoting", () => {
    expect(parseStdioArgsLine('-m "quoted server"')).toEqual([
      "-m",
      '"quoted',
      'server"',
    ])
  })

  it("round-trips args without embedded whitespace losslessly", () => {
    const args = ["--yes", "@example/server", "--port=8080"]
    expect(parseStdioArgsLine(buildStdioArgsLine(args))).toEqual(args)
  })

  it("documents that args with embedded whitespace round-trip lossily", () => {
    // An arg created via the API can hold embedded whitespace, e.g. a header
    // value. Joining on spaces then re-splitting on whitespace shreds it into
    // multiple tokens — the reason edit mode must not re-send unedited args.
    const args = ["--header=Authorization: Bearer x"]
    const line = buildStdioArgsLine(args)
    expect(line).toBe("--header=Authorization: Bearer x")
    expect(parseStdioArgsLine(line)).toEqual([
      "--header=Authorization:",
      "Bearer",
      "x",
    ])
  })
})

describe("shouldSendStdioFields", () => {
  it("sends neither field when nothing changed", () => {
    expect(
      shouldSendStdioFields({
        envDirty: false,
        argsDirty: false,
        templateApplied: false,
      })
    ).toEqual({ sendEnv: false, sendArgs: false })
  })

  it("sends only the edited field", () => {
    expect(
      shouldSendStdioFields({
        envDirty: true,
        argsDirty: false,
        templateApplied: false,
      })
    ).toEqual({ sendEnv: true, sendArgs: false })
    expect(
      shouldSendStdioFields({
        envDirty: false,
        argsDirty: true,
        templateApplied: false,
      })
    ).toEqual({ sendEnv: false, sendArgs: true })
  })

  it("forces both fields when a connection option template was applied", () => {
    // Switching options in edit mode resets the form and clears dirtyFields,
    // so the applied-template flag must send the new option's env and args.
    expect(
      shouldSendStdioFields({
        envDirty: false,
        argsDirty: false,
        templateApplied: true,
      })
    ).toEqual({ sendEnv: true, sendArgs: true })
  })
})

describe("stdio env row helpers", () => {
  it("shows keys for hidden values and safe template values", () => {
    expect(
      stdioEnvReadToRows({ API_TOKEN: "${{ SECRETS.api.TOKEN }}" }, [
        "API_TOKEN",
        "RAW_TOKEN",
      ])
    ).toEqual([
      { key: "API_TOKEN", value: "${{ SECRETS.api.TOKEN }}" },
      {
        key: "RAW_TOKEN",
        value: "",
        value_hidden: true,
        original_key: "RAW_TOKEN",
      },
    ])
  })

  it("preserves hidden values without serializing them as updates", () => {
    const rows = [
      { key: "API_TOKEN", value: "${{ SECRETS.api.TOKEN }}" },
      {
        key: "RAW_TOKEN",
        value: "",
        value_hidden: true,
        original_key: "RAW_TOKEN",
      },
    ]
    expect(stdioEnvRowsToRecord(rows)).toEqual({
      API_TOKEN: "${{ SECRETS.api.TOKEN }}",
    })
    expect(stdioEnvRowsToPreserveKeys(rows)).toEqual(["RAW_TOKEN"])
  })

  it("seeds hidden rows from server keys with an original_key", () => {
    expect(stdioEnvReadToRows(null, ["RAW_TOKEN"])).toEqual([
      {
        key: "RAW_TOKEN",
        value: "",
        value_hidden: true,
        original_key: "RAW_TOKEN",
      },
    ])
  })

  it("still preserves an unchanged hidden row", () => {
    const rows = stdioEnvReadToRows(null, ["API_TOKEN"])
    expect(stdioEnvRowsToPreserveKeys(rows)).toEqual(["API_TOKEN"])
    expect(stdioEnvRowsToRecord(rows)).toBeUndefined()
  })

  it("does not preserve a renamed hidden row under its new key", () => {
    // The row was seeded from server key API_TOKEN then renamed to NEW_TOKEN
    // without entering a value. It must not emit NEW_TOKEN as a preserve key
    // (the backend cannot resolve it, silently dropping the secret).
    const rows: MCPStdioEnvRow[] = [
      {
        key: "NEW_TOKEN",
        value: "",
        value_hidden: true,
        original_key: "API_TOKEN",
      },
    ]
    expect(stdioEnvRowsToPreserveKeys(rows)).toEqual([])
  })

  it("does not preserve a hidden row renamed to another stored hidden key", () => {
    // Renaming API_TOKEN -> RAW_TOKEN must not preserve the wrong value under
    // RAW_TOKEN. Only the genuinely-unchanged RAW_TOKEN row is preserved.
    const rows: MCPStdioEnvRow[] = [
      {
        key: "RAW_TOKEN",
        value: "",
        value_hidden: true,
        original_key: "API_TOKEN",
      },
      {
        key: "RAW_TOKEN",
        value: "",
        value_hidden: true,
        original_key: "RAW_TOKEN",
      },
    ]
    expect(stdioEnvRowsToPreserveKeys(rows)).toEqual(["RAW_TOKEN"])
  })
})

describe("stdioEnvRowsAreValid via schema", () => {
  const baseStdio = {
    ...MCP_INTEGRATION_FORM_DEFAULTS,
    name: "Vault",
    server_type: "stdio" as const,
    stdio_command: "npx",
    stdio_args_line: "mcp-vault",
  }

  it("accepts an unchanged hidden row with an empty value", () => {
    const result = buildMcpIntegrationFormSchema().safeParse({
      ...baseStdio,
      stdio_env: [
        {
          key: "API_TOKEN",
          value: "",
          value_hidden: true,
          original_key: "API_TOKEN",
        },
      ],
    })
    expect(result.success).toBe(true)
  })

  it("rejects a renamed hidden row left without a replacement value", () => {
    const result = buildMcpIntegrationFormSchema().safeParse({
      ...baseStdio,
      stdio_env: [
        {
          key: "NEW_TOKEN",
          value: "",
          value_hidden: true,
          original_key: "API_TOKEN",
        },
      ],
    })
    expect(result.success).toBe(false)
    if (!result.success) {
      const issue = result.error.issues.find((i) => i.path[0] === "stdio_env")
      expect(issue).toBeDefined()
    }
  })

  it("accepts a renamed hidden row once a replacement value is entered", () => {
    // In the dialog, entering a key or value clears value_hidden; model that
    // here as a plain visible row carrying the new value.
    const result = buildMcpIntegrationFormSchema().safeParse({
      ...baseStdio,
      stdio_env: [
        {
          key: "NEW_TOKEN",
          value: "${{ SECRETS.new.TOKEN }}",
          original_key: "API_TOKEN",
        },
      ],
    })
    expect(result.success).toBe(true)
  })

  it("rejects a visible row with an empty value", () => {
    const result = buildMcpIntegrationFormSchema().safeParse({
      ...baseStdio,
      stdio_env: [{ key: "PLAIN", value: "" }],
    })
    expect(result.success).toBe(false)
  })
})

describe("buildMcpIntegrationFormSchema url validation", () => {
  const baseStdio = {
    ...MCP_INTEGRATION_FORM_DEFAULTS,
    name: "Vault",
    server_type: "stdio" as const,
    stdio_command: "npx",
    stdio_args_line: "mcp-vault",
  }
  const urlEnvKeys = new Set(["VAULT_ADDR"])

  it("rejects a scheme-less url-typed stdio env var", () => {
    const result = buildMcpIntegrationFormSchema(urlEnvKeys).safeParse({
      ...baseStdio,
      stdio_env: [{ key: "VAULT_ADDR", value: "acme.example.net" }],
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
      stdio_env: [{ key: "VAULT_ADDR", value: "https://vault.example.net" }],
    })
    expect(result.success).toBe(true)
  })

  it("does not validate url shape when the spec declares no url keys", () => {
    const result = buildMcpIntegrationFormSchema(new Set()).safeParse({
      ...baseStdio,
      stdio_env: [{ key: "VAULT_ADDR", value: "acme.example.net" }],
    })
    expect(result.success).toBe(true)
  })

  it("accepts hidden stdio env values on edit", () => {
    const result = buildMcpIntegrationFormSchema(urlEnvKeys).safeParse({
      ...baseStdio,
      stdio_env: [
        {
          key: "VAULT_ADDR",
          value: "",
          value_hidden: true,
          original_key: "VAULT_ADDR",
        },
      ],
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

  it("prefills stdio command and environment rows", () => {
    const values = catalogEntryToFormValues({
      slug: "vault-mcp",
      name: "Vault",
      description: "Query Vault",
      connection_spec: {
        kind: "stdio_custom",
        server_type: "stdio",
        auth_type: "CUSTOM",
        stdio_command: "npx",
        stdio_args: ["mcp-vault"],
        stdio_env: [],
        packages: [],
        config_fields: [],
        credentials: [
          {
            key: "VAULT_ADDR",
            label: "Vault URL",
            description: "Vault base URL",
            required: true,
            secret: false,
            target: "stdio_env",
            placeholder: "https://vault.example.net",
          },
          {
            key: "VAULT_TOKEN",
            label: "Vault token",
            description: "Vault auth token",
            required: true,
            secret: true,
            target: "stdio_env",
          },
        ],
      },
    } as unknown as Parameters<typeof catalogEntryToFormValues>[0])

    expect(values.stdio_command).toBe("npx")
    expect(values.stdio_args_line).toBe("mcp-vault")
    expect(values.stdio_env).toEqual([
      { key: "VAULT_ADDR", value: "https://vault.example.net" },
      { key: "VAULT_TOKEN", value: "" },
    ])
  })
})
