import {
  type AgentOtelForm,
  agentOtelConfigToEnvMap,
  envMapToAgentOtelConfig,
  envMapToForm,
  envTextToForm,
  formToEnvMap,
  formToEnvText,
  validateAgentOtelHeaderEntries,
  validateEnvMap,
  validateEnvText,
  validateForm,
} from "@/lib/agent-otel"

describe("envMapToForm", () => {
  it("pulls first-class keys into dedicated fields", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://collector.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "60000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_METRICS_EXPORTER: "otlp",
    })

    expect(form.endpoint).toBe("https://collector.example.com")
    expect(form.metricIntervalMs).toBe("60000")
    expect(form.signals).toEqual({ traces: true, metrics: true, logs: false })
    expect(form.advancedEnv).toBe("")
  })

  it("keeps non-first-class keys in the advanced editor", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_RESOURCE_ATTRIBUTES: "service.name=agent",
      OTEL_LOG_USER_PROMPTS: "true",
    })

    expect(form.endpoint).toBe("https://c.example.com")
    // Sorted KEY=value text of the remaining keys.
    expect(form.advancedEnv).toBe(
      "OTEL_LOG_USER_PROMPTS=true\nOTEL_RESOURCE_ATTRIBUTES=service.name=agent"
    )
  })

  it("maps typed exporter values to signal toggles", () => {
    const form = envMapToForm({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "none",
      OTEL_METRICS_EXPORTER: "otlp",
    })

    expect(form.signals).toEqual({ traces: false, metrics: true, logs: false })
    expect(form.advancedEnv).toBe("")
  })
})

describe("formToEnvMap", () => {
  const base: AgentOtelForm = {
    endpoint: "",
    metricIntervalMs: "",
    signals: { traces: false, metrics: false, logs: false },
    advancedEnv: "",
  }

  it("omits empty first-class fields entirely", () => {
    expect(formToEnvMap(base)).toEqual({})
  })

  it("writes trimmed first-class fields and otlp exporters for on signals", () => {
    const env = formToEnvMap({
      ...base,
      endpoint: "  https://c.example.com  ",
      metricIntervalMs: " 30000 ",
      signals: { traces: true, metrics: false, logs: true },
    })

    expect(env).toEqual({
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_METRIC_EXPORT_INTERVAL: "30000",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_LOGS_EXPORTER: "otlp",
    })
  })

  it("lets first-class fields win over advanced on key collision", () => {
    const env = formToEnvMap({
      ...base,
      endpoint: "https://first-class.example.com",
      advancedEnv: "OTEL_EXPORTER_OTLP_ENDPOINT=https://advanced.example.com",
    })

    expect(env.OTEL_EXPORTER_OTLP_ENDPOINT).toBe(
      "https://first-class.example.com"
    )
  })

  it("removes the exporter when a signal is unchecked", () => {
    const form = envMapToForm({
      OTEL_METRICS_EXPORTER: "otlp",
    })
    form.signals.metrics = false

    expect(formToEnvMap(form)).toEqual({})
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
    },
    {
      OTEL_EXPORTER_OTLP_ENDPOINT: "https://c.example.com",
      OTEL_TRACES_EXPORTER: "otlp",
      OTEL_LOG_USER_PROMPTS: "true",
    },
    {},
  ])("env -> form -> env is identity for %o", (env) => {
    expect(formToEnvMap(envMapToForm(env))).toEqual(env)
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

describe("Raw mode helpers", () => {
  it("formToEnvText serializes the whole form as sorted KEY=value text", () => {
    const text = formToEnvText({
      endpoint: "https://c.example.com",
      metricIntervalMs: "",
      signals: { traces: true, metrics: false, logs: false },
      advancedEnv: "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
    })

    expect(text).toBe(
      [
        "OTEL_EXPORTER_OTLP_ENDPOINT=https://c.example.com",
        "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
        "OTEL_TRACES_EXPORTER=otlp",
      ].join("\n")
    )
  })

  it("form -> text -> form is identity through the Raw round-trip", () => {
    const form: AgentOtelForm = {
      endpoint: "https://c.example.com",
      metricIntervalMs: "60000",
      signals: { traces: true, metrics: true, logs: false },
      advancedEnv: "OTEL_RESOURCE_ATTRIBUTES=service.name=agent",
    }
    expect(envTextToForm(formToEnvText(form))).toEqual(form)
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
      endpoint: "",
      metricIntervalMs: "",
      signals: { traces: true, metrics: false, logs: false },
      advancedEnv: "",
    }
    const issues = validateForm(form)
    expect(issues.length).toBeGreaterThan(0)
    expect(issues[0]).toContain("OTEL_TRACES_EXPORTER=otlp needs")
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

  it("skips endpoint requirements when telemetry is disabled", () => {
    const options = { requireOtlpEndpoint: false }
    expect(validateEnvMap({ OTEL_LOGS_EXPORTER: "otlp" }, options)).toEqual([])
    expect(validateEnvText("OTEL_LOGS_EXPORTER=otlp", options)).toEqual([])
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
})
