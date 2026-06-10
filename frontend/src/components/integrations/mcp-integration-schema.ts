import { z } from "zod"
import type {
  MCPConnectionCredential,
  MCPConnectionSpec,
  PlatformMCPCatalogRead,
} from "@/client"

/**
 * Form schema for the MCP integration create/edit dialog.
 *
 * Kept in its own file because the schema, defaults, refinements, and helper
 * predicates are substantial and the dialog component is already large.
 */

export const SERVER_TYPES = [
  {
    value: "http",
    label: "URL (HTTP/SSE)",
    description: "Connect to an MCP server via HTTP or SSE endpoint",
  },
  {
    value: "stdio",
    label: "Stdio",
    description: "Run a command that spawns an MCP server (e.g., npx)",
  },
] as const

export const AUTH_TYPES = [
  {
    value: "OAUTH2",
    label: "OAuth 2.0",
    description: "Use MCP OAuth discovery or a Tracecat OAuth integration",
  },
  {
    value: "CUSTOM",
    label: "API key / token",
    description: "API key, bearer token, or custom headers (JSON)",
  },
  {
    value: "NONE",
    label: "No Authentication",
    description: "No authentication required (for self-hosted)",
  },
] as const

export const ALLOWED_COMMANDS = [
  "npx",
  "uvx",
  "python",
  "python3",
  "node",
] as const

export function isAllowedCommand(
  command: string
): command is (typeof ALLOWED_COMMANDS)[number] {
  return ALLOWED_COMMANDS.includes(command as (typeof ALLOWED_COMMANDS)[number])
}

/**
 * Validate that a string parses to a JSON object with only string values.
 * Used for both custom HTTP headers and stdio env vars — both end up as
 * `Record<string, string>` on the wire.
 */
export function isValidStringMap(
  value: string,
  { allowEmpty = false }: { allowEmpty?: boolean } = {}
): boolean {
  try {
    const parsed = JSON.parse(value) as unknown
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      return false
    }
    for (const headerValue of Object.values(
      parsed as Record<string, unknown>
    )) {
      if (typeof headerValue !== "string") {
        return false
      }
      if (!allowEmpty && headerValue.trim() === "") {
        return false
      }
    }
    return true
  } catch {
    return false
  }
}

/**
 * Normalize an OAuth client credential key for lenient comparisons, e.g.
 * "Client-Secret" and "client_secret" normalize to the same value.
 */
export function normalizeOAuthClientKey(key: string) {
  return key
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
}

/**
 * Return the labels of credentials the spec marks required for the OAuth
 * client target but that are empty (or missing) in the pasted JSON. The JSON
 * shape itself is validated by the form schema before this runs, so parse
 * failures are treated as "nothing missing" here.
 */
export function missingRequiredOAuthClientCredentials(
  spec: MCPConnectionSpec,
  value: string
): string[] {
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(value) as Record<string, unknown>
  } catch {
    return []
  }
  const valuesByKey = new Map(
    Object.entries(parsed).map(([key, entryValue]) => [
      normalizeOAuthClientKey(key),
      typeof entryValue === "string" ? entryValue.trim() : "",
    ])
  )
  const missing: string[] = []
  for (const credential of spec.credentials ?? []) {
    if (credential.target !== "oauth_client" || !credential.required) {
      continue
    }
    if (!valuesByKey.get(normalizeOAuthClientKey(credential.key))) {
      missing.push(credential.label || credential.key)
    }
  }
  return missing
}

export const mcpIntegrationFormSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(3, { message: "Name must be at least 3 characters long" })
      .max(255, { message: "Name must be 255 characters or fewer" }),
    description: z
      .string()
      .trim()
      .max(512, { message: "Description must be 512 characters or fewer" })
      .optional()
      .or(z.literal("")),
    // Server type
    server_type: z.enum(["http", "stdio"]),
    // HTTP-type fields
    server_uri: z.string().trim().optional().or(z.literal("")),
    auth_type: z.enum(["OAUTH2", "CUSTOM", "NONE"]),
    oauth_setup: z.enum([
      "mcp_discovery",
      "oauth_client",
      "existing_integration",
    ]),
    oauth_integration_id: z.string().uuid().optional().or(z.literal("")),
    oauth_client_credentials: z.string().trim().optional().or(z.literal("")),
    custom_credentials: z.string().trim().optional().or(z.literal("")),
    // Stdio-type fields
    stdio_command: z.string().trim().optional().or(z.literal("")),
    stdio_args: z.array(
      z.object({
        value: z.string(),
      })
    ),
    stdio_env: z.string().trim().optional().or(z.literal("")),
    // General fields
    timeout: z.coerce.number().int().min(1).max(300).optional(),
    catalog_slug: z.string().optional().or(z.literal("")),
    connection_option_id: z.string().optional().or(z.literal("")),
  })
  // HTTP-type validation
  .refine(
    (data) => {
      if (data.server_type === "http") {
        if (!data.server_uri || data.server_uri.trim() === "") {
          return false
        }
        try {
          new URL(data.server_uri)
          return true
        } catch {
          return false
        }
      }
      return true
    },
    {
      message: "Valid server URL is required for HTTP-type servers",
      path: ["server_uri"],
    }
  )
  .refine(
    (data) => {
      if (
        data.server_type === "http" &&
        data.auth_type === "OAUTH2" &&
        data.oauth_setup === "existing_integration"
      ) {
        return !!data.oauth_integration_id && data.oauth_integration_id !== ""
      }
      return true
    },
    {
      message: "OAuth integration is required for OAuth 2.0 authentication",
      path: ["oauth_integration_id"],
    }
  )
  .refine(
    (data) => {
      if (data.server_type === "http" && data.auth_type === "CUSTOM") {
        if (!data.custom_credentials || data.custom_credentials.trim() === "") {
          return false
        }
        return isValidStringMap(data.custom_credentials)
      }
      return true
    },
    {
      message:
        "Custom credentials must be a valid JSON object with string values",
      path: ["custom_credentials"],
    }
  )
  .refine(
    (data) => {
      if (
        data.server_type === "http" &&
        data.auth_type === "OAUTH2" &&
        data.oauth_setup === "oauth_client"
      ) {
        return (
          !!data.oauth_client_credentials &&
          data.oauth_client_credentials.trim() !== "" &&
          isValidStringMap(data.oauth_client_credentials, { allowEmpty: true })
        )
      }
      return true
    },
    {
      message: "OAuth client credentials must be a valid JSON object",
      path: ["oauth_client_credentials"],
    }
  )
  .refine(
    (data) => {
      if (
        data.server_type === "http" &&
        data.auth_type === "OAUTH2" &&
        data.custom_credentials &&
        data.custom_credentials.trim() !== ""
      ) {
        return isValidStringMap(data.custom_credentials)
      }
      return true
    },
    {
      message:
        "Additional headers must be a valid JSON object with string values",
      path: ["custom_credentials"],
    }
  )
  // Stdio-type validation
  .refine(
    (data) => {
      if (data.server_type === "stdio") {
        if (!data.stdio_command || data.stdio_command.trim() === "") {
          return false
        }
        return isAllowedCommand(data.stdio_command.trim())
      }
      return true
    },
    {
      message: `Command must be one of: ${ALLOWED_COMMANDS.join(", ")}`,
      path: ["stdio_command"],
    }
  )
  .refine(
    (data) => {
      if (
        data.server_type === "stdio" &&
        data.stdio_env &&
        data.stdio_env.trim() !== ""
      ) {
        return isValidStringMap(data.stdio_env)
      }
      return true
    },
    {
      message:
        "Environment variables must be a valid JSON object with string values",
      path: ["stdio_env"],
    }
  )

export type MCPIntegrationFormValues = z.infer<typeof mcpIntegrationFormSchema>

export const MCP_INTEGRATION_FORM_DEFAULTS: MCPIntegrationFormValues = {
  name: "",
  description: "",
  server_type: "http",
  server_uri: "",
  auth_type: "NONE",
  oauth_setup: "mcp_discovery",
  oauth_integration_id: "",
  oauth_client_credentials: "",
  custom_credentials: "",
  stdio_command: "",
  stdio_args: [],
  stdio_env: "",
  timeout: 30,
  catalog_slug: "",
  connection_option_id: "",
}

/**
 * Build a credentials JSON template from a catalog entry's declared
 * credentials, so the user sees the expected keys (with secret values left as
 * placeholders to fill in). Returns "" when there are no credentials.
 */
function credentialsTemplate(
  spec: MCPConnectionSpec | null | undefined,
  targets: MCPConnectionCredential["target"][]
): string {
  const allowedTargets = new Set(targets)
  const creds = (spec?.credentials ?? []).filter((cred) =>
    allowedTargets.has(cred.target)
  )
  if (creds.length === 0) {
    return ""
  }
  const obj: Record<string, string> = {}
  for (const cred of creds) {
    obj[cred.key] = ""
  }
  return JSON.stringify(obj, null, 2)
}

function hasConfigTarget(
  spec: MCPConnectionSpec | null | undefined,
  target: MCPConnectionCredential["target"]
): boolean {
  return Boolean(
    (spec?.credentials ?? []).some((cred) => cred.target === target) ||
      (spec?.config_fields ?? []).some((field) => field.target === target)
  )
}

function stdioEnvTemplate(spec: MCPConnectionSpec | null | undefined): string {
  const obj: Record<string, string> = {}
  for (const cred of spec?.credentials ?? []) {
    if (cred.target === "stdio_env" && cred.required) {
      obj[cred.key] = ""
    }
  }
  for (const field of spec?.config_fields ?? []) {
    if (field.target === "stdio_env" && field.required) {
      obj[field.key] = ""
    }
  }
  if (isStdioConnectionSpec(spec)) {
    for (const key of spec.stdio_env ?? []) {
      obj[key] = ""
    }
  }
  return Object.keys(obj).length > 0 ? JSON.stringify(obj, null, 2) : ""
}

/**
 * Narrow a generated catalog connection spec to stdio variants.
 */
function isStdioConnectionSpec(
  spec: MCPConnectionSpec | null | undefined
): spec is Extract<MCPConnectionSpec, { server_type?: "stdio" }> {
  return spec?.server_type === "stdio"
}

/**
 * Pick a stdio command the form will accept. Catalog packages may use runners
 * outside {@link ALLOWED_COMMANDS}; prefer the first allowed one, otherwise
 * fall back to the declared stdio_command if it is allowed.
 */
function pickStdioCommand(spec: MCPConnectionSpec | null | undefined): {
  command: string
  args: string[]
} {
  if (!isStdioConnectionSpec(spec)) {
    return { command: "", args: [] }
  }
  const allowedPackage = (spec.packages ?? []).find((pkg) =>
    isAllowedCommand(pkg.command)
  )
  if (allowedPackage) {
    return { command: allowedPackage.command, args: allowedPackage.args ?? [] }
  }
  if (spec.stdio_command && isAllowedCommand(spec.stdio_command)) {
    return { command: spec.stdio_command, args: spec.stdio_args ?? [] }
  }
  return { command: "", args: spec.stdio_args ?? [] }
}

/**
 * Map a catalog entry to MCP integration form values, prefilling everything we
 * can derive from its connection metadata. Used when a catalog card is clicked
 * so the user starts from a populated form they can override.
 */
export function catalogEntryToFormValues(
  entry: PlatformMCPCatalogRead,
  optionId?: string
): MCPIntegrationFormValues {
  const option = (entry.connection_options ?? []).find(
    (candidate) => candidate.id === optionId
  )
  const spec = option?.connection_spec ?? entry.connection_spec
  const serverType: "http" | "stdio" =
    spec?.server_type === "stdio" ? "stdio" : "http"
  const authType: "OAUTH2" | "CUSTOM" | "NONE" =
    spec?.auth_type === "OAUTH2" || spec?.auth_type === "CUSTOM"
      ? spec.auth_type
      : "NONE"
  const httpCredentialsJson = credentialsTemplate(spec, ["http_header"])
  const oauthClientCredentialsJson = credentialsTemplate(spec, ["oauth_client"])
  const requiresExistingOAuth =
    authType === "OAUTH2" && hasConfigTarget(spec, "oauth_client")
  const stdioEnvJson = stdioEnvTemplate(spec)
  const stdio = serverType === "stdio" ? pickStdioCommand(spec) : null
  const serverUri =
    spec?.server_type === "http" && "server_uri" in spec ? spec.server_uri : ""

  return {
    ...MCP_INTEGRATION_FORM_DEFAULTS,
    name: entry.name,
    description: entry.description,
    server_type: serverType,
    server_uri: serverUri,
    auth_type: authType,
    oauth_setup: requiresExistingOAuth ? "oauth_client" : "mcp_discovery",
    oauth_integration_id: "",
    oauth_client_credentials: oauthClientCredentialsJson,
    // HTTP CUSTOM/OAUTH2 headers OR stdio env get the credential-key template.
    custom_credentials: serverType === "http" ? httpCredentialsJson : "",
    stdio_command: stdio?.command ?? "",
    stdio_args: (stdio?.args ?? []).map((value) => ({ value })),
    stdio_env: serverType === "stdio" ? stdioEnvJson : "",
    catalog_slug: entry.slug,
    connection_option_id: option?.id ?? "",
  }
}
