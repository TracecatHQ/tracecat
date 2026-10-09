import type {
  AgentPresetCreate,
  AgentPresetRead,
  AgentPresetUpdate,
} from "@/client"
import { getApiErrorDetail } from "@/lib/errors"
import { slugify } from "@/lib/utils"

export type AgentPresetFormMode = "create" | "edit"

/** Return a readable preset error, including the effective tool limit. */
export function getAgentPresetErrorMessage(
  error: unknown,
  fallback: string
): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "body" in error &&
    typeof error.body === "object" &&
    error.body !== null &&
    "detail" in error.body
  ) {
    const detail = error.body.detail
    if (
      typeof detail === "object" &&
      detail !== null &&
      "code" in detail &&
      detail.code === "agent_tool_limit_exceeded" &&
      "tool_count" in detail &&
      typeof detail.tool_count === "number" &&
      "max_tools" in detail &&
      typeof detail.max_tools === "number"
    ) {
      return `This agent has ${detail.tool_count} tools; the limit is ${detail.max_tools}.`
    }
  }
  return getApiErrorDetail(error) ?? fallback
}

/**
 * Backend preset fields whose change makes the API cut a new preset version.
 * Mirrors `AgentPresetService.EXECUTION_FIELDS` in
 * `tracecat/agent/preset/service.py`. Keep both sides in sync.
 */
export const AGENT_PRESET_PUBLISHING_FIELDS: ReadonlySet<string> = new Set([
  "instructions",
  "model_name",
  "model_provider",
  "catalog_id",
  "base_url",
  "output_type",
  "actions",
  "namespaces",
  "tool_approvals",
  "mcp_integrations",
  "library_skills",
  "agents",
  "retries",
  "enable_thinking",
  "enable_internet_access",
])

export function buildSkillCommandItemValue({
  id,
  name,
  description,
}: {
  id: string
  name: string
  description?: string | null
}): string {
  const safeDescription = buildCommandSearchSegment(description ?? "")
  return ["skill", id, name, safeDescription].filter(Boolean).join(":")
}

function buildCommandSearchSegment(value: string): string {
  return value
    .normalize("NFKC")
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/[-\s]+/g, "-")
    .replace(/^-+|-+$/g, "")
}

export function getDuplicateItemName(name: string, fallback: string): string {
  const trimmedName = name.trim()
  return `Copy of ${trimmedName || fallback}`
}

export function buildDuplicateAgentSlug(
  slug: string,
  existingSlugs: Iterable<string>
): string {
  const normalizedSourceSlug = slugify(slug.trim(), "-") || "agent"
  const baseSlug =
    slugify(`copy-of-${normalizedSourceSlug}`, "-") || "copy-of-agent"
  const slugSet = new Set(existingSlugs)

  if (!slugSet.has(baseSlug)) {
    return baseSlug
  }

  let suffix = 2
  while (slugSet.has(`${baseSlug}-${suffix}`)) {
    suffix += 1
  }
  return `${baseSlug}-${suffix}`
}

export function buildDuplicateAgentPresetPayload(
  preset: AgentPresetRead,
  existingSlugs: Iterable<string>
): AgentPresetCreate {
  return {
    name: getDuplicateItemName(preset.name, "agent"),
    slug: buildDuplicateAgentSlug(preset.slug || preset.name, existingSlugs),
    description: preset.description ?? null,
    instructions: preset.instructions ?? null,
    model_name: preset.model_name,
    model_provider: preset.model_provider,
    base_url: preset.base_url ?? null,
    output_type: preset.output_type ?? null,
    actions: preset.actions ?? null,
    namespaces: preset.namespaces ?? null,
    tool_approvals: preset.tool_approvals ?? null,
    mcp_integrations: preset.mcp_integrations ?? null,
    library_skills: preset.library_skills ?? null,
    agents: preset.agents,
    retries: preset.retries,
    enable_thinking: preset.enable_thinking,
    enable_internet_access: preset.enable_internet_access,
  }
}

export function buildAgentPresetUpdatePayload(
  payload: AgentPresetCreate,
  { skillsChanged }: { skillsChanged: boolean }
): AgentPresetUpdate {
  const updatePayload: AgentPresetUpdate = { ...payload }
  if (!skillsChanged) {
    delete updatePayload.skills
  }
  return updatePayload
}

export function canSubmitAgentPresetForm({
  mode,
  isDirty,
  name,
  modelProvider,
  modelName,
}: {
  mode: AgentPresetFormMode
  isDirty: boolean
  name: string
  modelProvider: string
  modelName: string
}) {
  const hasRequiredFields =
    name.trim().length > 0 &&
    modelProvider.trim().length > 0 &&
    modelName.trim().length > 0

  if (mode === "edit") {
    return isDirty && hasRequiredFields
  }

  return hasRequiredFields
}
