import { z } from "zod"
import type {
  AgentCustomProviderCreate,
  AgentCustomProviderRead,
  AgentCustomProviderUpdate,
  CustomProviderType,
} from "@/client"

/**
 * Ordered list of selectable custom provider types shown in the wizard picker.
 */
export const CUSTOM_PROVIDER_TYPES: readonly CustomProviderType[] = [
  "generic_openai_compatible",
  "litellm",
  "ollama",
] as const

/**
 * Static presentation metadata for a custom provider type.
 */
export interface CustomProviderTypeOption {
  value: CustomProviderType
  label: string
  description: string
  /** Icon id understood by {@link ProviderIcon}. */
  iconId: string
}

const CUSTOM_PROVIDER_TYPE_OPTIONS: Record<
  CustomProviderType,
  CustomProviderTypeOption
> = {
  generic_openai_compatible: {
    value: "generic_openai_compatible",
    label: "OpenAI-compatible",
    description:
      "Any endpoint that speaks the OpenAI API, such as vLLM or a self-hosted gateway.",
    iconId: "custom",
  },
  litellm: {
    value: "litellm",
    label: "LiteLLM",
    description:
      "A LiteLLM proxy. Point at the proxy base URL and Tracecat forwards requests through it.",
    iconId: "litellm",
  },
  ollama: {
    value: "ollama",
    label: "Ollama",
    description:
      "A local or remote Ollama server. No API key required; models are discovered from the gateway.",
    iconId: "ollama",
  },
}

/**
 * Return the presentation metadata for a custom provider type.
 */
export function getCustomProviderTypeOption(
  type: CustomProviderType
): CustomProviderTypeOption {
  return CUSTOM_PROVIDER_TYPE_OPTIONS[type]
}

/**
 * Human-readable label for a custom provider type.
 */
export function getCustomProviderTypeLabel(
  type: CustomProviderType | null | undefined
): string {
  if (!type) {
    return "Custom"
  }
  return CUSTOM_PROVIDER_TYPE_OPTIONS[type]?.label ?? "Custom"
}

/**
 * Derive the {@link ProviderIcon} id from the stored provider `type` field.
 *
 * This replaces the old name/URL substring heuristic: the icon now reflects the
 * persisted type rather than guessing from the display name or base URL.
 */
export function getCustomProviderIconId(
  type: CustomProviderType | null | undefined
): string {
  if (!type) {
    return "custom"
  }
  return CUSTOM_PROVIDER_TYPE_OPTIONS[type]?.iconId ?? "custom"
}

/**
 * Whether the credential fields (API key, auth header) apply to this type.
 *
 * Ollama needs no credentials; the backend injects a placeholder key.
 */
export function typeSupportsCredentials(type: CustomProviderType): boolean {
  return type !== "ollama"
}

/**
 * Whether the passthrough control is user-configurable for this type.
 *
 * Passthrough is a free toggle for every type now; the wizard prefills a
 * per-type default but the user can freely change it.
 */
export function typeSupportsPassthrough(_type: CustomProviderType): boolean {
  return true
}

/**
 * The prefilled passthrough default when a type is first picked in the wizard.
 *
 * LiteLLM and Ollama default on (both natively serve the Anthropic passthrough
 * endpoint); generic defaults off. Prefill only, freely changeable afterward.
 */
export function typeDefaultPassthrough(type: CustomProviderType): boolean {
  return type === "litellm" || type === "ollama"
}

export const customProviderSchema = z
  .object({
    type: z.enum(["generic_openai_compatible", "litellm", "ollama"]),
    displayName: z.string().trim().min(1, "Name is required"),
    baseUrl: z.union([z.string().url(), z.literal(""), z.undefined()]),
    apiKeyHeader: z.string().trim().optional(),
    apiKey: z.string().optional(),
    customHeadersJson: z.string().optional(),
    passthrough: z.boolean(),
  })
  .superRefine((value, ctx) => {
    const raw = value.customHeadersJson?.trim()
    if (!raw) {
      return
    }

    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Custom headers must be a JSON object.",
          path: ["customHeadersJson"],
        })
        return
      }
      for (const [key, headerValue] of Object.entries(parsed)) {
        if (typeof key !== "string" || typeof headerValue !== "string") {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Custom headers must map string keys to string values.",
            path: ["customHeadersJson"],
          })
          return
        }
      }
    } catch {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Custom headers must be valid JSON.",
        path: ["customHeadersJson"],
      })
    }
  })

export type CustomProviderFormValues = z.infer<typeof customProviderSchema>

export const DEFAULT_CUSTOM_PROVIDER_VALUES: CustomProviderFormValues = {
  type: "generic_openai_compatible",
  displayName: "",
  baseUrl: "",
  apiKeyHeader: "",
  apiKey: "",
  customHeadersJson: "",
  passthrough: false,
}

/**
 * Build the initial form values for the edit dialog from a saved provider.
 */
export function getProviderDialogDefaults(
  provider: AgentCustomProviderRead | null
): CustomProviderFormValues {
  if (!provider) {
    return DEFAULT_CUSTOM_PROVIDER_VALUES
  }
  return {
    type: provider.type,
    displayName: provider.display_name,
    baseUrl: provider.base_url ?? "",
    apiKeyHeader: provider.api_key_header ?? "",
    apiKey: "",
    customHeadersJson: "",
    passthrough: provider.passthrough,
  }
}

function normalizeOptional(value: string | null | undefined): string | null {
  if (value == null) {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function parseCustomHeaders(
  value: string | null | undefined
): Record<string, string> | null {
  const trimmed = value?.trim()
  if (!trimmed) {
    return null
  }
  return JSON.parse(trimmed) as Record<string, string>
}

/**
 * Build the create payload, applying type-aware field visibility so hidden
 * credential fields never leak into the request (e.g. no key for Ollama).
 * Passthrough is sent verbatim for all types.
 */
export function buildProviderCreatePayload(
  values: CustomProviderFormValues
): AgentCustomProviderCreate {
  const supportsCredentials = typeSupportsCredentials(values.type)
  return {
    type: values.type,
    display_name: values.displayName.trim(),
    base_url: normalizeOptional(values.baseUrl),
    api_key_header: supportsCredentials
      ? normalizeOptional(values.apiKeyHeader)
      : null,
    api_key: supportsCredentials ? normalizeOptional(values.apiKey) : null,
    custom_headers: parseCustomHeaders(values.customHeadersJson),
    passthrough: values.passthrough,
  }
}

/**
 * Build the update payload, applying the same type-aware visibility rules.
 */
export function buildProviderUpdatePayload(
  values: CustomProviderFormValues
): AgentCustomProviderUpdate {
  const supportsCredentials = typeSupportsCredentials(values.type)
  const payload: AgentCustomProviderUpdate = {
    type: values.type,
    display_name: values.displayName.trim(),
    base_url: normalizeOptional(values.baseUrl),
    api_key_header: supportsCredentials
      ? normalizeOptional(values.apiKeyHeader)
      : null,
    passthrough: values.passthrough,
  }

  if (supportsCredentials) {
    const apiKey = normalizeOptional(values.apiKey)
    if (apiKey) {
      payload.api_key = apiKey
    }
  }
  const customHeaders = parseCustomHeaders(values.customHeadersJson)
  if (customHeaders) {
    payload.custom_headers = customHeaders
  }

  return payload
}
