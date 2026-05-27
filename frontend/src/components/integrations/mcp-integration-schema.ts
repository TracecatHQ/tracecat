import { z } from "zod"

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
    description: "Use existing OAuth integration (MCP standard)",
  },
  {
    value: "CUSTOM",
    label: "Custom",
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
export function isValidStringMap(value: string): boolean {
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
    }
    return true
  } catch {
    return false
  }
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
    oauth_integration_id: z.string().uuid().optional().or(z.literal("")),
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
      if (data.server_type === "http" && data.auth_type === "OAUTH2") {
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
        "Environment variables must be a valid JSON object with string values only",
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
  oauth_integration_id: "",
  custom_credentials: "",
  stdio_command: "",
  stdio_args: [],
  stdio_env: "",
  timeout: 30,
}
