import {
  type AgentOtelForm,
  agentOtelConfigToEnvMap,
  emptyAgentOtelForm,
  envMapToAgentOtelConfig,
  envMapToForm,
  formToEnvMap,
  resourceAttributeRowsToText,
  validateAgentOtelHeaderEntries,
  validateEnvMap,
  validateForm,
} from "@/lib/agent-otel"

describe("envMapToForm", () => {
  it("pulls connection keys into dedicated fields", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://collector.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "60000",
      OTEL_LOGS_EXPORT_INTERVAL: "5000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_METRICS_EXPORTER: "otlp",
    })

    expect(form.endpoint).toBe("https://collector.example.com")
    expect(form.metricIntervalMs).toBe("60000")
    expect(form.logsIntervalMs).toBe("5000")
    expect(form.signals).toEqual({ traces: true, metrics: true, logs: false })
  })

  it("promotes temporality and privacy flags to typed fields", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: "DELTA",
      OTEL_LOG_USER_PROMPTS: "true",
      OTEL_LOG_TOOL_DETAILS: "0",
    })

    expect(form.temporality).toBe("delta")
    expect(form.flags.logUserPrompts).toBe(true)
    expect(form.flags.logToolDetails).toBe(false)
    // Missing keys prefill with the runtime defaults.
    expect(form.flags.logToolContent).toBe(false)
    expect(form.flags.metricsIncludeSessionId).toBe(true)
  })

  it("expands resource attributes into decoded key/value rows", () => {
    const form = envMapToForm({
      OTEL_RESOURCE_ATTRIBUTES:
        "service.name=tracecat%20agent,key%2C1=value%3D1",
    })

    expect(
      form.resourceAttributes.map(({ name, value }) => ({ name, value }))
    ).toEqual([
      { name: "service.name", value: "tracecat agent" },
      { name: "key,1", value: "value=1" },
    ])
  })

  it("keeps malformed saved resource attributes editable", () => {
    const form = envMapToForm({ OTEL_RESOURCE_ATTRIBUTES: "missing-separator" })

    expect(form.resourceAttributes).toHaveLength(1)
    expect(form.resourceAttributes[0].name).toBe("missing-separator")
    expect(validateForm(form)).toEqual([
      "OTEL_RESOURCE_ATTRIBUTES must contain comma-separated key=value pairs.",
    ])
  })

  it("maps typed exporter values to signal toggles", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "none",
      OTEL_METRICS_EXPORTER: "otlp",
    })

    expect(form.signals).toEqual({ traces: false, metrics: true, logs: false })
  })
})

describe("formToEnvMap", () => {
  const base: AgentOtelForm = emptyAgentOtelForm()

  it("writes only the always-explicit flags for an empty form", () => {
    expect(formToEnvMap(base)).toEqual({
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "false",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
    })
  })

  it("writes trimmed fields and otlp exporters for on signals", () => {
    const env = formToEnvMap({
      ...base,
      endpoint: "  https://c.example.com  ",
      metricIntervalMs: " 30000 ",
      logsIntervalMs: " 5000 ",
      signals: { traces: true, metrics: false, logs: true },
    })

    expect(env).toEqual({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "30000",
      OTEL_LOGS_EXPORT_INTERVAL: "5000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_LOGS_EXPORTER: "otlp",
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "false",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
    })
  })

  it("omits unset temporality and writes every flag explicitly", () => {
    expect(
      formToEnvMap({
        ...base,
        temporality: "cumulative",
        flags: { ...base.flags, logUserPrompts: true },
      })
    ).toEqual({
      OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: "cumulative",
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "true",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
    })
  })

  it("serializes resource attribute rows and skips blank ones", () => {
    expect(
      formToEnvMap({
        ...base,
        resourceAttributes: [
          { id: "a", name: "service.name", value: "tracecat agent" },
          { id: "b", name: "  ", value: "  " },
        ],
      })
    ).toEqual({
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "false",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
      OTEL_RESOURCE_ATTRIBUTES: "service.name=tracecat%20agent",
    })
  })

  it("removes the exporter when a signal is unchecked", () => {
    const form = envMapToForm({
      OTEL_METRICS_EXPORTER: "otlp",
    })
    form.signals.metrics = false

    expect(formToEnvMap(form)).toEqual({
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "false",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
    })
  })
})

describe("resourceAttributeRowsToText", () => {
  it("keeps half-filled rows so validation can reject them", () => {
    expect(
      resourceAttributeRowsToText([
        { id: "a", name: "service.name", value: "" },
      ])
    ).toBe("service.name=")
  })
})

describe("round-trip fidelity", () => {
  it.each<Record<string, string>>([
    {
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "60000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_METRICS_EXPORTER: "otlp",
      OTEL_LOGS_EXPORTER: "otlp",
      OTEL_RESOURCE_ATTRIBUTES: "service.name=agent",
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "false",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
    },
    {
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: "delta",
      OTEL_LOGS_EXPORT_INTERVAL: "5000",
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "true",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
    },
  ])("env -> form -> env is identity for %o", (env) => {
    expect(formToEnvMap(envMapToForm(env))).toEqual(env)
  })

  it("completes missing flags with the runtime defaults", () => {
    expect(formToEnvMap(envMapToForm({}))).toEqual({
      OTEL_METRICS_INCLUDE_SESSION_ID: "true",
      OTEL_METRICS_INCLUDE_VERSION: "false",
      OTEL_METRICS_INCLUDE_ACCOUNT_UUID: "true",
      OTEL_LOG_USER_PROMPTS: "false",
      OTEL_LOG_TOOL_DETAILS: "false",
      OTEL_LOG_TOOL_CONTENT: "false",
    })
  })
})

describe("typed API adapters", () => {
  it("uses backend signal defaults when reading an omitted config", () => {
    expect(agentOtelConfigToEnvMap(undefined)).toEqual({
      OTEL_METRICS_EXPORTER: "otlp",
      OTEL_LOGS_EXPORTER: "otlp",
      OTEL_TRACES_EXPORTER: "none",
    })
  })

  it("round-trips every typed field through the env presentation", () => {
    const config = {
      enabled: true,
      endpoint: "https://collector.example.com",
      metrics_enabled: true,
      logs_enabled: false,
      traces_enabled: true,
      metrics_temporality: "delta" as const,
      metric_export_interval_ms: 60_000,
      logs_export_interval_ms: 5_000,
      metrics_include_session_id: false,
      metrics_include_version: true,
      metrics_include_account_uuid: false,
      log_user_prompts: true,
      log_tool_details: false,
      log_tool_content: true,
      resource_attributes: {
        "service.name": "tracecat agent",
        "key,1": "value=1",
      },
    }

    expect(
      envMapToAgentOtelConfig(true, agentOtelConfigToEnvMap(config))
    ).toEqual(config)
  })
})

describe("validateForm / validateEnvMap", () => {
  it("accepts a well-formed config", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "otlp",
    })
    expect(validateForm(form)).toEqual([])
  })

  it("flags an otlp signal with no endpoint", () => {
    const form: AgentOtelForm = {
      ...emptyAgentOtelForm(),
      signals: { traces: true, metrics: false, logs: false },
    }
    const issues = validateForm(form)
    expect(issues.length).toBeGreaterThan(0)
    expect(issues[0]).toContain("Add a collector endpoint")
  })

  it("rejects an unsupported key in the merged map", () => {
    expect(validateEnvMap({ NOT_AN_OTEL_VAR: "x" })).toEqual([
      "NOT_AN_OTEL_VAR is not supported.",
    ])
  })

  it("rejects a reserved key", () => {
    expect(validateEnvMap({ OTEL_EXPORTER_OTLP_HEADERS: "x" })).toEqual([
      "OTEL_EXPORTER_OTLP_HEADERS is managed by Tracecat.",
    ])
  })

  it("rejects a non-positive interval", () => {
    expect(validateEnvMap({ OTEL_METRIC_EXPORT_INTERVAL: "0" })).toEqual([
      "OTEL_METRIC_EXPORT_INTERVAL must be a positive integer.",
    ])
  })

  it.each(["1e3", "1.0", "0x10"])(
    "rejects non-decimal interval value %s",
    (value) => {
      expect(validateEnvMap({ OTEL_METRIC_EXPORT_INTERVAL: value })).toEqual([
        "OTEL_METRIC_EXPORT_INTERVAL must be a positive integer.",
      ])
    }
  )

  it.each([
    ["OTEL_METRIC_EXPORT_INTERVAL", "9007199254740993"],
    ["OTEL_LOGS_EXPORT_INTERVAL", `1${"0".repeat(400)}`],
  ])("rejects an unsafe-integer interval for %s", (key, value) => {
    expect(validateEnvMap({ [key]: value })).toEqual([
      `${key} must be at most ${Number.MAX_SAFE_INTEGER}.`,
    ])
  })

  it("accepts the largest safe interval", () => {
    expect(
      validateEnvMap({
        OTEL_METRIC_EXPORT_INTERVAL: String(Number.MAX_SAFE_INTEGER),
      })
    ).toEqual([])
  })

  it("accepts supported boolean spellings", () => {
    expect(validateEnvMap({ OTEL_LOG_USER_PROMPTS: "1" })).toEqual([])
    expect(validateEnvMap({ OTEL_LOG_USER_PROMPTS: "FALSE" })).toEqual([])
  })

  it.each([
    [
      "/v1/traces",
      "OTEL_EXPORTER_OTLP_ENDPOINT must be an absolute HTTP(S) URL.",
    ],
    [
      "ftp://collector.example.com",
      "OTEL_EXPORTER_OTLP_ENDPOINT must be an absolute HTTP(S) URL.",
    ],
    [
      "https://user:password@collector.example.com",
      "OTEL_EXPORTER_OTLP_ENDPOINT must not include credentials.",
    ],
    [
      "https://collector.example.com/v1/traces?token=secret",
      "OTEL_EXPORTER_OTLP_ENDPOINT must not include a query string.",
    ],
    [
      "https://collector.example.com/base#fragment",
      "OTEL_EXPORTER_OTLP_ENDPOINT must not include a fragment.",
    ],
  ])("rejects invalid collector endpoint %s", (endpoint, expected) => {
    expect(validateEnvMap({ OTEL_EXPORTER_OTLP_ENDPOINT: endpoint })).toEqual([
      expected,
    ])
  })

  it("rejects unsupported boolean values", () => {
    expect(validateEnvMap({ OTEL_LOG_USER_PROMPTS: "yes" })).toEqual([
      "OTEL_LOG_USER_PROMPTS supports true, false, 1, 0.",
    ])
  })

  it("accepts case-insensitive exporter and temporality values", () => {
    expect(
      validateEnvMap({
        OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
        OTEL_METRICS_EXPORTER: "OTLP",
        OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: "DELTA",
      })
    ).toEqual([])
  })

  it.each(["console", "otlp,console", "prometheus"])(
    "rejects unsupported exporter value %s",
    (value) => {
      expect(validateEnvMap({ OTEL_LOGS_EXPORTER: value })).toEqual([
        "OTEL_LOGS_EXPORTER supports otlp, none.",
      ])
    }
  )

  it("validates encoded resource attributes", () => {
    expect(
      validateEnvMap({
        OTEL_RESOURCE_ATTRIBUTES:
          "key%2C1=value%3D1,service.name=tracecat%20agent",
      })
    ).toEqual([])
    expect(
      validateEnvMap({ OTEL_RESOURCE_ATTRIBUTES: "missing-separator" })
    ).toEqual([
      "OTEL_RESOURCE_ATTRIBUTES must contain comma-separated key=value pairs.",
    ])
  })

  it("accepts resource attribute names shadowing Object.prototype members", () => {
    expect(
      validateEnvMap({
        OTEL_RESOURCE_ATTRIBUTES: "toString=a,constructor=b,__proto__=c",
      })
    ).toEqual([])
    expect(
      validateEnvMap({ OTEL_RESOURCE_ATTRIBUTES: "toString=a,toString=b" })
    ).toEqual(["OTEL_RESOURCE_ATTRIBUTES contains duplicate key toString."])
  })

  it("skips endpoint requirements when telemetry is disabled", () => {
    const options = { requireOtlpEndpoint: false }
    expect(validateEnvMap({ OTEL_LOGS_EXPORTER: "otlp" }, options)).toEqual([])
  })
  it.each([
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
  ])("rejects relay-managed protocol key %s", (key) => {
    expect(validateEnvMap({ [key]: "http/protobuf" })).toEqual([
      `${key} is managed by Tracecat.`,
    ])
  })
})

describe("validateAgentOtelHeaderEntries", () => {
  it("rejects duplicate trimmed header names case-insensitively", () => {
    expect(
      validateAgentOtelHeaderEntries([
        { name: "Authorization", value: "Bearer first" },
        { name: " authorization ", value: "Bearer second" },
      ])
    ).toEqual(["Header name authorization is duplicated."])
  })

  it("accepts unique non-empty headers", () => {
    expect(
      validateAgentOtelHeaderEntries([
        { name: "Authorization", value: "Bearer token" },
        { name: "X-Tenant", value: "tenant" },
      ])
    ).toEqual([])
  })

  it("rejects header names that are not HTTP tokens", () => {
    expect(
      validateAgentOtelHeaderEntries([{ name: "Bad Header", value: "x" }])
    ).toEqual([
      "Header name Bad Header is not a valid HTTP header name (letters, digits, and !#$%&'*+-.^_`|~).",
    ])
  })

  it.each([["bad\nvalue"], ["café"]])(
    "rejects unsendable header value %j",
    (value) => {
      expect(
        validateAgentOtelHeaderEntries([{ name: "Authorization", value }])
      ).toEqual([
        "Header Authorization value must contain only printable ASCII characters.",
      ])
    }
  )
})

describe("endpoint delimiters", () => {
  it.each([
    ["https://c.example.com/base?", "must not include a query string."],
    ["https://c.example.com/base#", "must not include a fragment."],
  ])("rejects a bare delimiter in %s", (endpoint, message) => {
    expect(validateEnvMap({ OTEL_EXPORTER_OTLP_ENDPOINT: endpoint })).toEqual([
      `OTEL_EXPORTER_OTLP_ENDPOINT ${message}`,
    ])
  })
})
