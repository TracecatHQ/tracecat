import { render, screen } from "@testing-library/react"
import type { AgentPresetReadMinimal } from "@/client"
import {
  AgentMentionChip,
  MentionChip,
} from "@/components/tiptap-node/mention-node/mention-node"
import { useAgentPresets } from "@/hooks/use-agent-presets"

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: jest.fn(),
}))

const mockUseAgentPresets = useAgentPresets as jest.MockedFunction<
  typeof useAgentPresets
>

const PRESET_ID = "0f9d9f4c-1c2b-4f3a-9a1e-2b7c8d9e0f11"

function agentPreset(
  overrides: Partial<AgentPresetReadMinimal> = {}
): AgentPresetReadMinimal {
  return {
    id: PRESET_ID,
    workspace_id: "workspace-1",
    name: "Current Name",
    slug: "current-name",
    description: null,
    model_provider: "openai",
    model_name: "gpt-4o",
    created_at: "2024-01-01T00:00:00.000Z",
    updated_at: "2024-01-01T00:00:00.000Z",
    ...overrides,
  }
}

function mockPresets({
  presets,
  presetsIsLoading = false,
  presetsError = null,
}: {
  presets: AgentPresetReadMinimal[] | undefined
  presetsIsLoading?: boolean
  presetsError?: ReturnType<typeof useAgentPresets>["presetsError"]
}) {
  mockUseAgentPresets.mockReturnValue({
    presets,
    presetsIsLoading,
    presetsError,
    refetchPresets: jest.fn(),
  } as unknown as ReturnType<typeof useAgentPresets>)
}

beforeEach(() => {
  mockUseAgentPresets.mockReset()
})

describe("MentionChip", () => {
  it("marks the unavailable treatment with a stable data-state hook", () => {
    render(
      <MentionChip
        label="@Someone"
        state="unavailable"
        title="Agent preset unavailable"
      />
    )

    const chip = screen.getByTestId("mention-chip")
    expect(chip).toHaveAttribute("data-state", "unavailable")
    expect(chip).toHaveAttribute("title", "Agent preset unavailable")
    expect(chip.className).toContain("border-dashed")
  })

  it("uses the normal treatment for resolved and loading chips", () => {
    const { rerender } = render(
      <MentionChip label="@Someone" state="loading" />
    )
    expect(screen.getByTestId("mention-chip").className).not.toContain(
      "border-dashed"
    )

    rerender(<MentionChip label="@Someone" state="resolved" />)
    expect(screen.getByTestId("mention-chip").className).not.toContain(
      "border-dashed"
    )
  })
})

describe("AgentMentionChip", () => {
  it("renders the resolved preset name and drops a stale embedded label", () => {
    mockPresets({ presets: [agentPreset({ name: "Current Name" })] })

    render(<AgentMentionChip presetId={PRESET_ID} label="@Stale Label" />)

    const chip = screen.getByTestId("mention-chip")
    expect(chip).toHaveTextContent("@Current Name")
    expect(chip).toHaveAttribute("data-state", "resolved")
    expect(screen.queryByText("@Stale Label")).not.toBeInTheDocument()
  })

  it("falls back to the embedded label with the unavailable treatment for an unknown preset", () => {
    mockPresets({ presets: [agentPreset({ id: "some-other-id" })] })

    render(<AgentMentionChip presetId={PRESET_ID} label="@Deleted Agent" />)

    const chip = screen.getByTestId("mention-chip")
    expect(chip).toHaveTextContent("@Deleted Agent")
    expect(chip).toHaveAttribute("data-state", "unavailable")
    expect(chip).toHaveAttribute("title", "Agent preset unavailable")
  })

  it("treats a failed preset query as unavailable", () => {
    mockPresets({
      presets: undefined,
      presetsError: {
        status: 500,
        body: { detail: "boom" },
      } as unknown as ReturnType<typeof useAgentPresets>["presetsError"],
    })

    render(<AgentMentionChip presetId={PRESET_ID} label="@Some Agent" />)

    expect(screen.getByTestId("mention-chip")).toHaveAttribute(
      "data-state",
      "unavailable"
    )
  })

  it("renders the embedded label without the unavailable treatment while loading", () => {
    mockPresets({ presets: undefined, presetsIsLoading: true })

    render(<AgentMentionChip presetId={PRESET_ID} label="@Some Agent" />)

    const chip = screen.getByTestId("mention-chip")
    expect(chip).toHaveTextContent("@Some Agent")
    expect(chip).toHaveAttribute("data-state", "loading")
    expect(chip).not.toHaveAttribute("title")
  })

  it("stays in the loading treatment while presets are still undefined", () => {
    mockPresets({ presets: undefined, presetsIsLoading: false })

    render(<AgentMentionChip presetId={PRESET_ID} label="@Some Agent" />)

    expect(screen.getByTestId("mention-chip")).toHaveAttribute(
      "data-state",
      "loading"
    )
  })
})
