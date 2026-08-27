import type {
  AgentPresetCreate,
  AgentPresetRead,
  AgentPresetUpdate,
  AttachedSubagentRef,
} from "@/client"
import { slugify } from "@/lib/utils"

export type AgentPresetFormMode = "create" | "edit"

type AuthoredSubagentFormValue = {
  preset: string
  presetId?: string | null
  name?: string | null
  description?: string | null
  maxTurns?: string | null
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

/**
 * Builds the authored subagent config accepted by preset create/update APIs.
 *
 * `presetId` is deliberately local-only form state. The backend resolves the
 * authored preset slug to its internal ResourceHead identifier.
 */
export function buildAuthoredAgentsConfig({
  enabled,
  subagents,
}: {
  enabled: boolean
  subagents: readonly AuthoredSubagentFormValue[]
}): NonNullable<AgentPresetCreate["agents"]> {
  if (!enabled) {
    return { enabled: false }
  }

  const authoredSubagents = subagents
    .map((subagent): AttachedSubagentRef | null => {
      const preset = subagent.preset.trim()
      if (!preset) {
        return null
      }

      const payload: AttachedSubagentRef = { preset }
      const name = normalizeOptionalText(subagent.name)
      const description = normalizeOptionalText(subagent.description)
      const maxTurns = parseOptionalPositiveInteger(subagent.maxTurns)

      if (name !== null) {
        payload.name = name
      }
      if (description !== null) {
        payload.description = description
      }
      if (maxTurns !== null) {
        payload.max_turns = maxTurns
      }

      return payload
    })
    .filter((subagent): subagent is AttachedSubagentRef => subagent !== null)

  return {
    enabled: true,
    subagents: authoredSubagents,
  }
}

function normalizeOptionalText(
  value: string | null | undefined
): string | null {
  const trimmed = value?.trim()
  return trimmed ? trimmed : null
}

function parseOptionalPositiveInteger(
  value: string | null | undefined
): number | null {
  const trimmed = value?.trim()
  return trimmed ? Number.parseInt(trimmed, 10) : null
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
