import { render, screen } from "@testing-library/react"
import {
  AgentPresetEventLink,
  getAgentPresetRef,
} from "@/components/executions/agent-preset-event-link"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"

const mockUseAgentPresets = jest.fn()

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: (...args: unknown[]) => mockUseAgentPresets(...args),
}))

let mockCanReadAgents: boolean | undefined = true

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => mockCanReadAgents,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const PRESET = { id: "preset-1", slug: "triage-agent", name: "Triage agent" }
const PRESET_REF = { presetId: PRESET.id, slug: PRESET.slug }

function event(
  actionName: string,
  actionInput: unknown
): WorkflowExecutionEventCompact {
  return {
    source_event_id: 1,
    schedule_time: "2026-01-01T00:00:00Z",
    curr_event_type: "CHILD_WORKFLOW_EXECUTION_STARTED",
    status: "STARTED",
    action_name: actionName,
    action_ref: "run_agent",
    action_input: actionInput,
  } as WorkflowExecutionEventCompact
}

describe("getAgentPresetRef", () => {
  it("reads the preset ID and slug from an agent run", () => {
    const input = {
      agent_preset_id: PRESET.id,
      agent_args: { preset_slug: PRESET.slug, user_prompt: "Triage" },
    }
    expect(getAgentPresetRef(event("ai.agent", input))).toEqual(PRESET_REF)
  })

  it("ignores agent runs without a preset", () => {
    const input = { agent_args: { user_prompt: "Hi" } }
    expect(getAgentPresetRef(event("ai.agent", input))).toBeNull()
  })

  it("ignores non-agent events with lookalike input", () => {
    const input = { agent_preset_id: PRESET.id }
    expect(getAgentPresetRef(event("__TRIGGER__", input))).toBeNull()
  })
})

describe("AgentPresetEventLink", () => {
  beforeEach(() => {
    mockUseAgentPresets.mockReset()
    mockCanReadAgents = true
  })

  it("links to the preset with its name and slug", () => {
    mockUseAgentPresets.mockReturnValue({ presets: [PRESET] })
    render(<AgentPresetEventLink presetRef={PRESET_REF} />)

    expect(screen.getByRole("link", { name: "Triage agent" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/agents/preset-1"
    )
    expect(screen.getByText("triage-agent")).toBeInTheDocument()
  })

  it("resolves a renamed preset by ID and keeps the run's slug", () => {
    mockUseAgentPresets.mockReturnValue({
      presets: [{ ...PRESET, slug: "triage-v2", name: "Triage v2" }],
    })
    render(<AgentPresetEventLink presetRef={PRESET_REF} />)

    expect(screen.getByRole("link", { name: "Triage v2" })).toBeInTheDocument()
    expect(screen.getByText("triage-agent")).toBeInTheDocument()
  })

  it("marks a deleted preset and keeps the run's slug", () => {
    mockUseAgentPresets.mockReturnValue({ presets: [] })
    render(<AgentPresetEventLink presetRef={PRESET_REF} />)

    expect(screen.queryByRole("link")).not.toBeInTheDocument()
    expect(screen.getByText("Deleted agent")).toBeInTheDocument()
    expect(screen.getByText("triage-agent")).toBeInTheDocument()
  })

  it("shows only the slug while presets load", () => {
    mockUseAgentPresets.mockReturnValue({ presets: undefined })
    render(<AgentPresetEventLink presetRef={PRESET_REF} />)

    expect(screen.queryByText("Deleted agent")).not.toBeInTheDocument()
    expect(screen.getByText("triage-agent")).toBeInTheDocument()
  })

  it("never links a slug-only run, since slugs are reusable", () => {
    mockUseAgentPresets.mockReturnValue({ presets: undefined })
    render(
      <AgentPresetEventLink presetRef={{ presetId: null, slug: PRESET.slug }} />
    )

    expect(mockUseAgentPresets).toHaveBeenCalledWith(
      "workspace-1",
      expect.objectContaining({ enabled: false })
    )
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
    expect(screen.getByText("triage-agent")).toBeInTheDocument()
  })

  it("skips the preset lookup without agent:read", () => {
    mockCanReadAgents = false
    mockUseAgentPresets.mockReturnValue({ presets: undefined })
    render(<AgentPresetEventLink presetRef={PRESET_REF} />)

    expect(mockUseAgentPresets).toHaveBeenCalledWith(
      "workspace-1",
      expect.objectContaining({ enabled: false })
    )
    expect(screen.queryByText("Deleted agent")).not.toBeInTheDocument()
    expect(screen.getByText("triage-agent")).toBeInTheDocument()
  })
})
