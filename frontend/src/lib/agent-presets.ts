import type { AgentPresetCreate, AgentPresetRead } from "@/client"
import { slugify } from "@/lib/utils"

export type AgentPresetFormMode = "create" | "edit"

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
    agents: preset.agents,
    retries: preset.retries,
    enable_thinking: preset.enable_thinking,
    enable_internet_access: preset.enable_internet_access,
  }
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
