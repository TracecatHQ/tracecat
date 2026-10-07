import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AgentPresetChatToggle } from "@/components/agents/agent-preset-chat-toggle"

const mockUpdateAgentPreset = jest.fn()
const mockUseAgentPreset = jest.fn()
const mockUseScopeCheck = jest.fn()
let mockPending = false

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPreset: () => mockUseAgentPreset(),
  useUpdateAgentPreset: () => ({
    updateAgentPreset: mockUpdateAgentPreset,
    updateAgentPresetIsPending: mockPending,
  }),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => mockUseScopeCheck(),
}))

describe("AgentPresetChatToggle", () => {
  beforeEach(() => {
    mockUseAgentPreset.mockReturnValue({ preset: { use_in_chat: false } })
    mockUseScopeCheck.mockReturnValue(true)
    mockUpdateAgentPreset.mockReset()
    mockUpdateAgentPreset.mockResolvedValue({})
    mockPending = false
  })

  it.each([false, true])(
    "persists the inverse of %s immediately",
    async (enabled) => {
      mockUseAgentPreset.mockReturnValue({ preset: { use_in_chat: enabled } })
      render(
        <AgentPresetChatToggle workspaceId="workspace-1" presetId="preset-1" />
      )
      const toggle = screen.getByRole("switch", { name: "Use in chat" })
      expect(toggle).toHaveAttribute("aria-checked", String(enabled))
      await userEvent.click(toggle)
      expect(mockUpdateAgentPreset).toHaveBeenCalledWith({
        presetId: "preset-1",
        use_in_chat: !enabled,
      })
    }
  )

  it.each(["permission", "pending", "loading", "saving"])(
    "disables for %s",
    (reason) => {
      if (reason === "permission") mockUseScopeCheck.mockReturnValue(false)
      if (reason === "pending") mockPending = true
      if (reason === "loading") mockUseAgentPreset.mockReturnValue({})
      render(
        <AgentPresetChatToggle
          workspaceId="workspace-1"
          presetId="preset-1"
          disabled={reason === "saving"}
        />
      )
      expect(screen.getByRole("switch", { name: "Use in chat" })).toBeDisabled()
      expect(mockUpdateAgentPreset).not.toHaveBeenCalled()
    }
  )

  it("handles a rejected update without changing the persisted state", async () => {
    mockUpdateAgentPreset.mockRejectedValue(new Error("Failed"))
    render(
      <AgentPresetChatToggle workspaceId="workspace-1" presetId="preset-1" />
    )
    await userEvent.click(screen.getByRole("switch", { name: "Use in chat" }))
    expect(screen.getByRole("switch", { name: "Use in chat" })).toHaveAttribute(
      "aria-checked",
      "false"
    )
  })
})
