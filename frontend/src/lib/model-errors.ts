import type { ModelErrorCode, ModelErrorDetail } from "@/client"

/** UI data part carrying a failed model request's structured detail. */
export const MODEL_ERROR_DATA_PART_TYPE = "data-model-error"

export const AGENT_SETTINGS_HREF = "/organization/settings/agent"

const MODEL_ERROR_CODES: readonly ModelErrorCode[] = [
  "credentials_missing",
  "provider_auth_failed",
  "model_not_enabled",
  "model_unavailable",
  "rate_limited",
  "budget_exceeded",
  "context_too_long",
  "provider_unreachable",
  "request_rejected",
  "unknown",
]

const PROVIDER_NAMES: Record<string, string> = {
  anthropic: "Anthropic",
  azure_ai: "Azure AI",
  azure_openai: "Azure OpenAI",
  bedrock: "AWS Bedrock",
  "custom-model-provider": "custom model provider",
  gemini: "Google Gemini",
  litellm: "LiteLLM",
  mistral: "Mistral",
  ollama: "Ollama",
  openai: "OpenAI",
  openrouter: "OpenRouter",
  vertex_ai: "Vertex AI",
  vllm: "vLLM",
}

/** A provider's name as a noun; templates add articles and capitals. */
export function providerDisplayName(provider: string | null | undefined) {
  if (!provider) return "model provider"
  return PROVIDER_NAMES[provider] ?? provider.replaceAll("_", " ")
}

function isModelErrorCode(value: unknown): value is ModelErrorCode {
  return (
    typeof value === "string" &&
    (MODEL_ERROR_CODES as readonly string[]).includes(value)
  )
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null
}

/**
 * Validate a `data-model-error` payload. An unknown code from a newer server
 * reads as `unknown` so the error still renders.
 */
export function parseModelErrorDetail(data: unknown): ModelErrorDetail | null {
  if (!data || typeof data !== "object") return null
  const raw = data as Record<string, unknown>
  if (typeof raw.code !== "string") return null
  return {
    code: isModelErrorCode(raw.code) ? raw.code : "unknown",
    kind: optionalString(raw.kind) as ModelErrorDetail["kind"],
    provider: optionalString(raw.provider),
    model: optionalString(raw.model),
    status_code: typeof raw.status_code === "number" ? raw.status_code : null,
    request_id: optionalString(raw.request_id),
    provider_error_code: optionalString(raw.provider_error_code),
  }
}

export interface ModelErrorCopy {
  /** What went wrong, naming the provider and model. */
  title: string
  /** What to do about it. */
  action: string
  /** Settings page that fixes it, when one does. */
  settingsHref?: string
}

/** Capitalize a sentence that may start with a lowercase provider name. */
export function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Actionable copy for a model error code. */
export function describeModelError(detail: ModelErrorDetail): ModelErrorCopy {
  const copy = describeModelErrorRaw(detail)
  return { ...copy, title: sentence(copy.title) }
}

function describeModelErrorRaw(detail: ModelErrorDetail): ModelErrorCopy {
  const provider = providerDisplayName(detail.provider)
  const model = detail.model ?? "the selected model"
  switch (detail.code) {
    case "credentials_missing":
      return {
        title: `No ${provider} credentials are set up for ${model}.`,
        action: "Add them in agent settings.",
        settingsHref: AGENT_SETTINGS_HREF,
      }
    case "provider_auth_failed":
      return {
        title: `${provider} rejected the API key for ${model}.`,
        action: "Update the key in agent settings.",
        settingsHref: AGENT_SETTINGS_HREF,
      }
    case "model_not_enabled":
      return {
        title: `${model} isn't enabled for this workspace.`,
        action: "Enable it in agent settings, or pick another model.",
        settingsHref: AGENT_SETTINGS_HREF,
      }
    case "model_unavailable":
      return {
        title: `${provider} can't find ${model}, or this key has no access to it.`,
        action: "Check the model and its access in agent settings.",
        settingsHref: AGENT_SETTINGS_HREF,
      }
    case "rate_limited":
      return {
        title: `${provider} is rate limiting requests for ${model}.`,
        action: "Wait a moment, then retry.",
      }
    case "budget_exceeded":
      return {
        title: `${provider} account for ${model} is out of quota or budget.`,
        action: "Check billing with the provider or the configured budget.",
      }
    case "context_too_long":
      return {
        title: `This conversation is too long for ${model}.`,
        action: "Start a new chat to continue.",
      }
    case "provider_unreachable":
      return {
        title: `Couldn't reach ${provider}.`,
        action:
          "Retry in a moment. If it keeps failing, check the provider's base URL in agent settings.",
        settingsHref: AGENT_SETTINGS_HREF,
      }
    case "request_rejected":
      return {
        title: `${provider} rejected the request for ${model}.`,
        action: "Check the model settings, then retry.",
        settingsHref: AGENT_SETTINGS_HREF,
      }
    default:
      return {
        title: `The request to ${model} failed.`,
        action: "Retry. If it keeps failing, check the model settings.",
        settingsHref: AGENT_SETTINGS_HREF,
      }
  }
}

/** Short technical facts for a collapsible detail row. */
export function modelErrorFacts(detail: ModelErrorDetail): string[] {
  const facts: string[] = [`code: ${detail.code}`]
  if (detail.status_code != null) facts.push(`HTTP ${detail.status_code}`)
  if (detail.provider_error_code) {
    facts.push(`provider error: ${detail.provider_error_code}`)
  }
  if (detail.request_id) facts.push(`request ID: ${detail.request_id}`)
  if (detail.kind) facts.push(`kind: ${detail.kind}`)
  return facts
}
