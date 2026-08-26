import { stringify } from "yaml"
import type {
  AgentPresetCreate,
  AgentPresetSkillBindingRead,
  AgentPresetVersionRead,
  CompatibleAttachedSubagentRef,
  OutputType,
} from "@/client"

/**
 * Renders an agent preset as two virtual files, `instructions.md` and
 * `config.yaml`, so presets can reuse the file-tree + inline-diff version
 * history built for skills.
 *
 * A saved version and the live draft both flow through the single normalizer
 * ({@link agentPresetExecutionFieldsToDocumentInput}) and the single stringifier
 * ({@link buildAgentPresetVirtualFiles}). Symmetry is therefore structural: the
 * two sides cannot drift without changing shared code.
 *
 * `name`, `slug`, and `description` are deliberately absent from the document.
 * `AgentPresetVersionRead` does not carry them and they are not execution
 * fields, so the backend cuts no version when only they change. The document is
 * the versioned surface by construction, which means a metadata-only edit
 * produces a byte-identical `config.yaml` and the diff honestly shows nothing.
 */

/** Mirrors `DEFAULT_RETRIES` in `agent-presets-builder.tsx`. */
const DEFAULT_RETRIES = 3

/** Virtual path of the instructions file. */
export const AGENT_PRESET_INSTRUCTIONS_PATH = "instructions.md"

/** Virtual path of the configuration file. */
export const AGENT_PRESET_CONFIG_PATH = "config.yaml"

/**
 * A single tool approval, normalized from the server's `Record<string, boolean>`
 * into a sortable list so object key order cannot leak into the diff.
 */
export interface AgentPresetToolApproval {
  /** Fully qualified tool name. */
  tool: string
  /** Whether the tool runs without an approval prompt. */
  allow: boolean
}

/**
 * A subagent attachment as rendered in the document.
 *
 * `preset_id` / `preset_version_id` are intentionally excluded: they are opaque
 * UUIDs that the draft may not have resolved yet, so including them would show a
 * change on every unsaved edit.
 */
export interface AgentPresetSubagentEntry {
  /** Display name override, or null to use the preset's own name. */
  name: string | null
  /** Preset slug the subagent is backed by. */
  preset: string
  /** Pinned preset version, or null to track the latest. */
  presetVersion: number | null
  /** Maximum turns allowed for this subagent, or null for the default. */
  maxTurns: number | null
  /** Description override, or null. */
  description: string | null
}

/**
 * A skill attachment as rendered in the document.
 *
 * The version matters: restoring a version copies its exact historical skill
 * pins back onto the preset head (`_restore_head_skill_bindings_from_version`
 * on the backend), so two sides pinning the same skill at different versions
 * must render differently or the diff hides a real change.
 */
export interface AgentPresetSkillEntry {
  /** Skill name, resolved through the current workspace skill names. */
  name: string
  /** Pinned skill version, or null when the draft has not pinned one yet. */
  version: number | null
}

/**
 * The single normalized intermediate both converters produce.
 *
 * Every field is total — no optionals, no `undefined` — because absent values
 * must still render as explicit `null` or `[]` in the YAML. Omitting a key
 * would shift lines and produce phantom add/remove pairs in the diff.
 */
export interface AgentPresetDocumentInput {
  /** Instructions body, rendered as `instructions.md`. */
  instructions: string
  /** Model provider id. */
  modelProvider: string
  /** Model name. */
  modelName: string
  /** Custom base URL, or null. */
  baseUrl: string | null
  /** Model catalog id, or null. */
  catalogId: string | null
  /** Structured output type. Object schemas are recursively key-sorted. */
  outputType: OutputType | null
  /** Allowed action names, sorted. */
  actions: string[]
  /** Allowed namespaces, sorted. */
  namespaces: string[]
  /** Attached MCP integration ids, sorted. */
  mcpIntegrations: string[]
  /** Tool approvals, sorted by tool name. */
  toolApprovals: AgentPresetToolApproval[]
  /** Whether subagents are enabled. */
  subagentsEnabled: boolean
  /** Attached subagents, sorted by name then preset. Empty when disabled. */
  subagents: AgentPresetSubagentEntry[]
  /** Attached skills with their version pins, sorted by name then version. */
  skills: AgentPresetSkillEntry[]
  /** Retry budget. */
  retries: number
  /** Whether extended thinking is enabled. */
  enableThinking: boolean
  /** Whether internet access is enabled. */
  enableInternetAccess: boolean
}

/**
 * The execution fields shared by `AgentPresetCreate` and
 * `AgentPresetVersionRead`. Skills differ in shape between the two and are
 * passed separately.
 */
type AgentPresetExecutionFields = Omit<
  AgentPresetCreate,
  "name" | "slug" | "description" | "skills"
>

/** Sorts a list of set-like strings. Membership is semantic, order is not. */
function sortStrings(values: readonly string[]): string[] {
  return [...values].sort((left, right) => left.localeCompare(right))
}

/**
 * Recursively sorts object keys.
 *
 * `output_type` may be a user-typed JSON schema whose key order is unstable
 * between the form and the server round-trip.
 */
function sortObjectKeysDeep(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortObjectKeysDeep)
  }
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>
    const sorted: Record<string, unknown> = {}
    for (const key of Object.keys(record).sort((left, right) =>
      left.localeCompare(right)
    )) {
      sorted[key] = sortObjectKeysDeep(record[key])
    }
    return sorted
  }
  return value
}

/**
 * Normalizes `output_type`, which is either a scalar string union member or an
 * object schema.
 */
function normalizeOutputType(
  outputType: OutputType | null | undefined
): OutputType | null {
  if (outputType === null || outputType === undefined) {
    return null
  }
  if (typeof outputType === "string") {
    return outputType
  }
  return sortObjectKeysDeep(outputType) as OutputType
}

/**
 * Converts the tool approval record into a sorted list.
 *
 * Object key order differs between the react-hook-form field array and the
 * server's JSON, which is the single biggest source of diff noise.
 */
function normalizeToolApprovals(
  toolApprovals: { [key: string]: boolean } | null | undefined
): AgentPresetToolApproval[] {
  if (!toolApprovals) {
    return []
  }
  return Object.entries(toolApprovals)
    .map(([tool, allow]) => ({ tool, allow }))
    .sort((left, right) => left.tool.localeCompare(right.tool))
}

/**
 * Normalizes subagent attachments to a fixed shape, dropping resolved ids and
 * sorting by the label the user actually sees.
 */
function normalizeSubagents(
  subagents: readonly CompatibleAttachedSubagentRef[] | null | undefined
): AgentPresetSubagentEntry[] {
  if (!subagents) {
    return []
  }
  return subagents
    .map((subagent) => ({
      name: subagent.name ?? null,
      preset: subagent.preset,
      // Head refs carry no version pin - they always track the latest.
      presetVersion:
        "preset_version" in subagent ? (subagent.preset_version ?? null) : null,
      maxTurns: subagent.max_turns ?? null,
      description: subagent.description ?? null,
    }))
    .sort((left, right) =>
      (left.name ?? left.preset).localeCompare(right.name ?? right.preset)
    )
}

/**
 * A skill attachment before name resolution: the id, the historical name the
 * binding carried (null when unknown, e.g. a freshly attached draft skill), and
 * the pinned version (null when not yet pinned).
 */
interface RawSkillBinding {
  skillId: string
  fallbackName: string | null
  version: number | null
}

/**
 * Resolves skill bindings to `{ name, version }` entries.
 *
 * Names must resolve identically on both sides of the diff, so every entry
 * goes through the same chain: the CURRENT workspace name from
 * `skillNamesById` first, then the binding's stored `skill_name`, then the raw
 * UUID. Never prefer the historical `skill_name` while the current name is
 * known — a renamed skill would then diff forever against the draft side.
 *
 * Sorted by name, with version as a tiebreak so duplicates are deterministic.
 */
function normalizeSkills(
  bindings: readonly RawSkillBinding[],
  skillNamesById: ReadonlyMap<string, string>
): AgentPresetSkillEntry[] {
  return bindings
    .map((binding) => ({
      name:
        skillNamesById.get(binding.skillId) ??
        binding.fallbackName ??
        binding.skillId,
      version: binding.version,
    }))
    .sort(
      (left, right) =>
        left.name.localeCompare(right.name) ||
        (left.version ?? -1) - (right.version ?? -1)
    )
}

/**
 * Normalizes the execution fields shared by both preset representations.
 *
 * Both public converters funnel through here, so the draft and the saved
 * version cannot normalize differently.
 */
function agentPresetExecutionFieldsToDocumentInput(
  fields: AgentPresetExecutionFields,
  skillBindings: readonly RawSkillBinding[],
  skillNamesById: ReadonlyMap<string, string>
): AgentPresetDocumentInput {
  const subagentsEnabled = fields.agents?.enabled ?? false
  // Mirrors `formValuesToAgentsPayload` in the builder: a disabled toggle drops
  // the attachments entirely, so a stale list must not show up in the diff.
  const subagents = subagentsEnabled
    ? normalizeSubagents(fields.agents?.subagents)
    : []

  return {
    instructions: fields.instructions ?? "",
    modelProvider: fields.model_provider,
    modelName: fields.model_name,
    baseUrl: fields.base_url ?? null,
    catalogId: fields.catalog_id ?? null,
    outputType: normalizeOutputType(fields.output_type),
    actions: sortStrings(fields.actions ?? []),
    namespaces: sortStrings(fields.namespaces ?? []),
    mcpIntegrations: sortStrings(fields.mcp_integrations ?? []),
    toolApprovals: normalizeToolApprovals(fields.tool_approvals),
    subagentsEnabled,
    subagents,
    skills: normalizeSkills(skillBindings, skillNamesById),
    // `form.getValues()` returns raw input and `retries` is a `z.coerce.number()`
    // field, so mid-edit it can still be the string "3".
    retries: Number(fields.retries ?? DEFAULT_RETRIES),
    enableThinking: fields.enable_thinking ?? false,
    enableInternetAccess: fields.enable_internet_access ?? false,
  }
}

/**
 * Normalizes a saved preset version into the shared document input.
 *
 * Skill pins come straight from the version's bindings: restoring copies those
 * exact `skill_version` pins back to the head, so they are part of the diff.
 */
export function agentPresetVersionToDocumentInput(
  version: AgentPresetVersionRead,
  skillNamesById: ReadonlyMap<string, string>
): AgentPresetDocumentInput {
  return agentPresetExecutionFieldsToDocumentInput(
    version,
    (version.skills ?? []).map((skill) => ({
      skillId: skill.skill_id,
      fallbackName: skill.skill_name,
      version: skill.skill_version,
    })),
    skillNamesById
  )
}

/**
 * Normalizes a draft create/update payload into the shared document input.
 *
 * The draft form tracks only `skill_id`, so each skill's pinned version is
 * resolved from `headBindingsBySkillId` — the CURRENT preset head's bindings,
 * keyed by `skill_id`. A skill already attached resolves to the version the
 * backend currently has pinned; a skill the user just attached in the form has
 * no pin yet and renders as `version: null`.
 */
export function agentPresetPayloadToDocumentInput(
  payload: AgentPresetCreate,
  skillNamesById: ReadonlyMap<string, string>,
  headBindingsBySkillId: ReadonlyMap<string, AgentPresetSkillBindingRead>
): AgentPresetDocumentInput {
  return agentPresetExecutionFieldsToDocumentInput(
    payload,
    (payload.skills ?? []).map((skill) => {
      const headBinding = headBindingsBySkillId.get(skill.skill_id)
      return {
        skillId: skill.skill_id,
        fallbackName: headBinding?.skill_name ?? null,
        version: headBinding?.skill_version ?? null,
      }
    }),
    skillNamesById
  )
}

/**
 * Normalizes markdown whitespace so the badge in the restore dialog agrees with
 * the diff body underneath it.
 *
 * The TipTap markdown editor round-trips the instructions source on mount, which
 * collapses blank-line runs: a preset stored as `"## Task\n\n\n\n## Context"`
 * comes back out of the form as `"## Task\n\n## Context"`. Raw string equality
 * therefore reported `modified` on a pristine form with zero user edits, while
 * `ProseDiff` — which uses jsdiff's whitespace-insensitive `diffWords` — rendered
 * no highlighted changes at all.
 *
 * Both sides of the comparison funnel through here, so they converge on the same
 * bytes. The normalizer:
 * - rewrites CRLF and lone CR to `\n`;
 * - strips trailing spaces and tabs from the end of every line;
 * - collapses runs of three or more newlines to exactly two, leaving at most one
 *   blank line between blocks.
 *
 * Accepted trade-off: a genuine whitespace-only edit to the instructions now
 * reads as "unchanged" in the restore dialog. That is the prose-mode semantics
 * already settled for this feature, and making the badge agree with the body is
 * the point. This normalization only affects what the restore dialog compares
 * and displays — it never changes what gets published.
 */
function normalizeInstructionsWhitespace(instructions: string): string {
  return instructions
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
}

/**
 * Renders the normalized input as the preset's two virtual files.
 *
 * Key order is fixed by object construction and preserved by `yaml.stringify`,
 * which keeps the ordering explicit and greppable instead of hidden behind a
 * `sortMapEntries` option. Every key is always emitted so line positions stay
 * stable across versions.
 */
export function buildAgentPresetVirtualFiles(input: AgentPresetDocumentInput): {
  instructions: string
  config: string
} {
  const config = {
    model: {
      provider: input.modelProvider,
      name: input.modelName,
      base_url: input.baseUrl,
      catalog_id: input.catalogId,
    },
    output_type: input.outputType,
    actions: input.actions,
    namespaces: input.namespaces,
    mcp_integrations: input.mcpIntegrations,
    tool_approvals: input.toolApprovals.map((approval) => ({
      tool: approval.tool,
      allow: approval.allow,
    })),
    subagents: {
      enabled: input.subagentsEnabled,
      agents: input.subagents.map((subagent) => ({
        name: subagent.name,
        preset: subagent.preset,
        preset_version: subagent.presetVersion,
        max_turns: subagent.maxTurns,
        description: subagent.description,
      })),
    },
    skills: input.skills.map((skill) => ({
      name: skill.name,
      version: skill.version,
    })),
    runtime: {
      retries: input.retries,
      enable_thinking: input.enableThinking,
      enable_internet_access: input.enableInternetAccess,
    },
  }

  return {
    // Matches the editor's existing behaviour: markdown whitespace normalized to
    // the prose diff's own semantics, trailing whitespace trimmed, then exactly
    // one trailing newline.
    instructions: `${normalizeInstructionsWhitespace(input.instructions).trimEnd()}\n`,
    // `lineWidth: 0` disables line folding; otherwise a long base URL wraps at a
    // nondeterministic column.
    config: stringify(config, { lineWidth: 0 }),
  }
}
