import type {
  AgentPresetCreate,
  AgentPresetSkillBindingRead,
  AgentPresetVersionRead,
  AnyAttachedSubagentRef,
} from "@/client"
import {
  agentPresetPayloadToDocumentInput,
  agentPresetVersionToDocumentInput,
  buildAgentPresetVirtualFiles,
} from "@/lib/agent-preset-document"

const LONG_BASE_URL =
  "https://gateway.example.com/very/long/path/segment/that/exceeds/the/default/yaml/line/width/for/folding/v1"

const SKILL_ALPHA_ID = "11111111-1111-1111-1111-111111111111"
const SKILL_BETA_ID = "22222222-2222-2222-2222-222222222222"

const SKILL_NAMES: ReadonlyMap<string, string> = new Map([
  [SKILL_ALPHA_ID, "alpha-skill"],
  [SKILL_BETA_ID, "beta-skill"],
])

/** The saved version's skill bindings, deliberately unsorted. */
const VERSION_SKILLS: AgentPresetSkillBindingRead[] = [
  {
    skill_id: SKILL_BETA_ID,
    skill_version_id: "77777777-7777-7777-7777-777777777777",
    skill_name: "beta-skill",
    skill_version: 3,
  },
  {
    skill_id: SKILL_ALPHA_ID,
    skill_version_id: "88888888-8888-8888-8888-888888888888",
    skill_name: "alpha-skill",
    skill_version: 1,
  },
]

/**
 * The current preset head's bindings, keyed by skill id. Pins match the saved
 * version so the round-trip symmetry fixtures agree on both sides.
 */
const HEAD_BINDINGS: ReadonlyMap<string, AgentPresetSkillBindingRead> = new Map(
  VERSION_SKILLS.map((binding) => [binding.skill_id, binding] as const)
)

/**
 * Single source of truth for the execution fields. Both fixtures below are
 * derived from it so the round-trip symmetry test cannot drift.
 */
const SHARED_EXECUTION = {
  instructions: "Investigate the alert.\n\nThen report.   \n\n\n",
  model_name: "claude-opus-4",
  model_provider: "anthropic",
  catalog_id: "catalog-1",
  base_url: LONG_BASE_URL,
  output_type: {
    type: "object",
    required: ["summary"],
    properties: { summary: { type: "string" } },
  },
  actions: ["tools.slack.post_message", "tools.aws.list_buckets"],
  namespaces: ["tools.slack", "tools.aws"],
  tool_approvals: {
    "tools.slack.post_message": true,
    "tools.aws.list_buckets": false,
  },
  mcp_integrations: ["mcp-beta", "mcp-alpha"],
  retries: 5,
  enable_thinking: true,
  enable_internet_access: false,
}

/** The subagents the draft form knows about: slugs only, unresolved. */
const DRAFT_SUBAGENTS: AnyAttachedSubagentRef[] = [
  {
    preset: "triage",
    preset_version: 2,
    name: "Triage",
    description: "Triages alerts",
    max_turns: 4,
  },
  {
    preset: "writer",
    preset_version: null,
    name: "Alpha writer",
    description: null,
    max_turns: null,
  },
]

/**
 * The same subagents as stored on a saved version: resolved UUIDs attached, and
 * in a different order.
 */
const VERSION_SUBAGENTS: AnyAttachedSubagentRef[] = [
  {
    ...DRAFT_SUBAGENTS[1],
    preset_id: "33333333-3333-3333-3333-333333333333",
    preset_version_id: "44444444-4444-4444-4444-444444444444",
  },
  {
    ...DRAFT_SUBAGENTS[0],
    preset_id: "55555555-5555-5555-5555-555555555555",
    preset_version_id: "66666666-6666-6666-6666-666666666666",
  },
]

function buildVersion(
  overrides: Partial<AgentPresetVersionRead> = {}
): AgentPresetVersionRead {
  return {
    ...SHARED_EXECUTION,
    agents: { subagents: VERSION_SUBAGENTS },
    skills: VERSION_SKILLS,
    restore_skills: VERSION_SKILLS,
    id: "version-1",
    preset_id: "preset-1",
    workspace_id: "workspace-1",
    version: 3,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

function buildPayload(
  overrides: Partial<AgentPresetCreate> = {}
): AgentPresetCreate {
  return {
    ...SHARED_EXECUTION,
    agents: { subagents: DRAFT_SUBAGENTS },
    skills: [{ skill_id: SKILL_ALPHA_ID }, { skill_id: SKILL_BETA_ID }],
    name: "Alert triage agent",
    slug: "alert-triage-agent",
    description: "Triages inbound alerts",
    ...overrides,
  }
}

function renderVersion(
  version: AgentPresetVersionRead,
  skillNames: ReadonlyMap<string, string> = SKILL_NAMES
) {
  return buildAgentPresetVirtualFiles(
    agentPresetVersionToDocumentInput(version, skillNames)
  )
}

function renderPayload(
  payload: AgentPresetCreate,
  skillNames: ReadonlyMap<string, string> = SKILL_NAMES,
  headBindings: ReadonlyMap<string, AgentPresetSkillBindingRead> = HEAD_BINDINGS
) {
  return buildAgentPresetVirtualFiles(
    agentPresetPayloadToDocumentInput(payload, skillNames, headBindings)
  )
}

function configLine(config: string, key: string): string | undefined {
  return config.split("\n").find((line) => line.includes(`${key}:`))
}

describe("buildAgentPresetVirtualFiles round-trip symmetry", () => {
  it("renders a version and the equivalent draft payload identically", () => {
    const fromVersion = renderVersion(buildVersion())
    const fromPayload = renderPayload(buildPayload())

    expect(fromPayload.instructions).toBe(fromVersion.instructions)
    expect(fromPayload.config).toBe(fromVersion.config)
  })

  it("emits the fixed key order", () => {
    const { config } = renderVersion(buildVersion())
    const topLevelKeys = config
      .split("\n")
      .filter((line) => /^[a-z_]+:/.test(line))
      .map((line) => line.slice(0, line.indexOf(":")))

    expect(topLevelKeys).toEqual([
      "model",
      "output_type",
      "actions",
      "namespaces",
      "mcp_integrations",
      "tool_approvals",
      "subagents",
      "skills",
      "runtime",
    ])
  })

  it("trims trailing whitespace and ends instructions with one newline", () => {
    const { instructions } = renderVersion(buildVersion())
    expect(instructions).toBe("Investigate the alert.\n\nThen report.\n")
  })
})

describe("buildAgentPresetVirtualFiles determinism", () => {
  const baseline = renderPayload(buildPayload()).config

  it("ignores the order of actions and namespaces", () => {
    const shuffled = renderPayload(
      buildPayload({
        actions: [...SHARED_EXECUTION.actions].reverse(),
        namespaces: [...SHARED_EXECUTION.namespaces].reverse(),
        mcp_integrations: [...SHARED_EXECUTION.mcp_integrations].reverse(),
      })
    ).config
    expect(shuffled).toBe(baseline)
  })

  it("ignores tool approval key order", () => {
    const shuffled = renderPayload(
      buildPayload({
        tool_approvals: {
          "tools.aws.list_buckets": false,
          "tools.slack.post_message": true,
        },
      })
    ).config
    expect(shuffled).toBe(baseline)
  })

  it("ignores subagent order", () => {
    const shuffled = renderPayload(
      buildPayload({
        agents: {
          subagents: [...DRAFT_SUBAGENTS].reverse(),
        },
      })
    ).config
    expect(shuffled).toBe(baseline)
  })

  it("ignores output type object key order", () => {
    const shuffled = renderPayload(
      buildPayload({
        output_type: {
          properties: { summary: { type: "string" } },
          required: ["summary"],
          type: "object",
        },
      })
    ).config
    expect(shuffled).toBe(baseline)
  })

  it("ignores skill binding order", () => {
    const shuffled = renderPayload(
      buildPayload({
        skills: [{ skill_id: SKILL_BETA_ID }, { skill_id: SKILL_ALPHA_ID }],
      })
    ).config
    expect(shuffled).toBe(baseline)
  })

  it("ignores metadata-only changes", () => {
    const renamed = renderPayload(
      buildPayload({
        name: "Completely different name",
        slug: "completely-different-name",
        description: "A brand new description",
      })
    )
    expect(renamed.config).toBe(baseline)
    expect(renamed.instructions).toBe(
      renderPayload(buildPayload()).instructions
    )
  })
})

describe("buildAgentPresetVirtualFiles normalization", () => {
  it("coerces a mid-edit string retries value to a number", () => {
    const { config } = renderPayload(
      buildPayload({ retries: "3" as unknown as number })
    )
    expect(configLine(config, "retries")).toBe("  retries: 3")
  })

  it("does not fold a long base url", () => {
    const { config } = renderVersion(buildVersion())
    expect(configLine(config, "base_url")).toContain(LONG_BASE_URL)
  })

  it("falls back to the raw uuid for an unknown skill id", () => {
    const unknownId = "99999999-9999-9999-9999-999999999999"
    const { config } = renderPayload(
      buildPayload({ skills: [{ skill_id: unknownId }] }),
      new Map(),
      new Map()
    )
    expect(config).toContain(unknownId)
  })

  it("renders skill names with their pinned versions, sorted by name", () => {
    const { config } = renderVersion(buildVersion())
    expect(config).toContain(
      "skills:\n" +
        "  - name: alpha-skill\n" +
        "    version: 1\n" +
        "  - name: beta-skill\n" +
        "    version: 3\n"
    )
  })

  it("renders version null for a draft skill absent from the head bindings", () => {
    const { config } = renderPayload(
      buildPayload({ skills: [{ skill_id: SKILL_ALPHA_ID }] }),
      SKILL_NAMES,
      new Map()
    )
    expect(config).toContain(
      "skills:\n  - name: alpha-skill\n    version: null\n"
    )
  })

  it("excludes resolved subagent uuids", () => {
    const { config } = renderVersion(buildVersion())
    expect(config).not.toContain("preset_id")
    expect(config).not.toContain("preset_version_id")
    expect(config).toContain("preset_version: 2")
  })

  it("ignores the removed legacy enabled field", () => {
    const { config } = renderPayload(
      buildPayload({
        agents: { enabled: false, subagents: DRAFT_SUBAGENTS } as never,
      })
    )
    expect(config).not.toContain("enabled:")
    expect(config).toContain("preset: writer")
    expect(config).toContain("preset: triage")
  })

  it("emits explicit nulls and empty lists for a minimal preset", () => {
    const { instructions, config } = renderPayload({
      name: "Minimal",
      model_name: "claude-opus-4",
      model_provider: "anthropic",
    })

    expect(instructions).toBe("\n")
    expect(config).toBe(
      [
        "model:",
        "  provider: anthropic",
        "  name: claude-opus-4",
        "  base_url: null",
        "  catalog_id: null",
        "output_type: null",
        "actions: []",
        "namespaces: []",
        "mcp_integrations: []",
        "tool_approvals: []",
        "subagents:",
        "  agents: []",
        "skills: []",
        "runtime:",
        "  retries: 3",
        "  enable_thinking: false",
        "  enable_internet_access: false",
        "",
      ].join("\n")
    )
  })
})

describe("buildAgentPresetVirtualFiles skill version pins", () => {
  /**
   * Regression: the restore preview used the immutable historical Skill pin
   * instead of the current Skill head that backend restore actually selects.
   */
  it("uses the current-head restore projection instead of historical pins", () => {
    const fromVersion = renderVersion(
      buildVersion({
        skills: [
          {
            skill_id: SKILL_ALPHA_ID,
            skill_version_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            skill_name: "alpha-skill",
            skill_version: 5,
          },
        ],
        restore_skills: [
          {
            skill_id: SKILL_ALPHA_ID,
            skill_version_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            skill_name: "alpha-skill",
            skill_version: 2,
          },
        ],
      })
    )
    const fromPayload = renderPayload(
      buildPayload({ skills: [{ skill_id: SKILL_ALPHA_ID }] }),
      SKILL_NAMES,
      new Map([
        [
          SKILL_ALPHA_ID,
          {
            skill_id: SKILL_ALPHA_ID,
            skill_version_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            skill_name: "alpha-skill",
            skill_version: 2,
          },
        ],
      ])
    )

    expect(fromVersion.config).toBe(fromPayload.config)
    expect(fromVersion.config).toContain(
      "skills:\n  - name: alpha-skill\n    version: 2\n"
    )
  })

  it("does not diff a skill renamed since the version was cut", () => {
    // The workspace now calls alpha-skill something else; the version's
    // binding still stores the historical name. Both sides must resolve
    // through the CURRENT name or the rename diffs forever.
    const renamedNames: ReadonlyMap<string, string> = new Map([
      [SKILL_ALPHA_ID, "alpha-skill-v2"],
      [SKILL_BETA_ID, "beta-skill"],
    ])
    const fromVersion = renderVersion(buildVersion(), renamedNames)
    const fromPayload = renderPayload(buildPayload(), renamedNames)

    expect(fromVersion.config).toBe(fromPayload.config)
    expect(fromVersion.config).toContain("name: alpha-skill-v2")
  })
})

describe("buildAgentPresetVirtualFiles instructions whitespace", () => {
  function renderInstructions(instructions: string): string {
    return renderPayload(buildPayload({ instructions })).instructions
  }

  /**
   * Regression: the TipTap markdown editor round-trips the instructions source
   * on mount and collapses blank-line runs, so a pristine form holds
   * `"## Task\n\n## Context"` for a preset stored as `"## Task\n\n\n\n## Context"`.
   * Raw string equality then badged `instructions.md` as "Modified" while the
   * whitespace-insensitive prose diff showed nothing. Do not remove the
   * normalizer without first removing that round-trip.
   */
  it("converges the TipTap blank-line round-trip on identical bytes", () => {
    const stored = renderInstructions("## Task\n\n\n\n## Context\n\nbody")
    const roundTripped = renderInstructions("## Task\n\n## Context\n\nbody")

    expect(stored).toBe(roundTripped)
    expect(stored).toBe("## Task\n\n## Context\n\nbody\n")
  })

  it("strips trailing spaces from the end of every line", () => {
    expect(renderInstructions("a   \nb")).toBe("a\nb\n")
    expect(renderInstructions("a\t\nb  ")).toBe("a\nb\n")
  })

  it("normalizes CRLF and lone CR to LF", () => {
    expect(renderInstructions("a\r\nb\rc")).toBe("a\nb\nc\n")
  })

  it("collapses a long newline run to a single blank line", () => {
    expect(renderInstructions("a\n\n\n\n\n\nb")).toBe("a\n\nb\n")
  })

  it("still detects non-whitespace differences", () => {
    expect(renderInstructions("## Task")).not.toBe(
      renderInstructions("## Tasks")
    )
    expect(renderInstructions("a\n\nb")).not.toBe(renderInstructions("a\n\nc"))
  })
})
