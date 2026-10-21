import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AgentPresetChatToggle } from "@/components/agents/agent-preset-chat-toggle"
import { AgentPresetDetailHeaderActions } from "@/components/agents/agent-preset-detail-actions"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { AgentPresetDetailActionsState } from "@/providers/agent-preset-detail"

const mockUpdateAgentPreset = jest.fn()
const mockUseAgentPreset = jest.fn()
const mockUseScopeCheck = jest.fn()
const mockUseDetailContext = jest.fn()
let mockPending = false

jest.mock("@/providers/agent-preset-detail", () => ({
  useAgentPresetDetailContext: () => mockUseDetailContext(),
}))

jest.mock("@/components/agents/agent-preset-version-history", () => ({
  AgentPresetVersionHistory: () => <button type="button">History</button>,
}))

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPreset: () => mockUseAgentPreset(),
  useAgentPresetVersions: () => ({
    versions: [{ id: "version-1", version: 12 }],
  }),
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
        <AgentPresetChatToggle workspaceId="workspace-1" presetId="preset-1" />,
        { wrapper: TooltipProvider }
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
        />,
        { wrapper: TooltipProvider }
      )
      expect(screen.getByRole("switch", { name: "Use in chat" })).toBeDisabled()
      expect(mockUpdateAgentPreset).not.toHaveBeenCalled()
    }
  )

  it("handles a rejected update without changing the persisted state", async () => {
    mockUpdateAgentPreset.mockRejectedValue(new Error("Failed"))
    render(
      <AgentPresetChatToggle workspaceId="workspace-1" presetId="preset-1" />,
      { wrapper: TooltipProvider }
    )
    await userEvent.click(screen.getByRole("switch", { name: "Use in chat" }))
    expect(screen.getByRole("switch", { name: "Use in chat" })).toHaveAttribute(
      "aria-checked",
      "false"
    )
  })

  it("orders chat visibility, discard, Push, versions, then Publish", async () => {
    const actions: AgentPresetDetailActionsState = {
      workspaceId: "workspace-1",
      presetId: "preset-1",
      currentVersionId: "version-1",
      getDraftPayload: () => null,
      isSaving: false,
      canSubmit: true,
      submitLabel: "Save version",
      submit: jest.fn(),
      isDirty: true,
      cutsVersion: true,
      discard: jest.fn(),
    }
    mockUseDetailContext.mockReturnValue({ actions })
    render(
      <TooltipProvider>
        <AgentPresetDetailHeaderActions
          syncActions={<button type="button">Push</button>}
        />
      </TooltipProvider>
    )
    const toggle = screen.getByRole("switch", { name: "Use in chat" })
    const discard = screen.getByRole("button", { name: "Discard" })
    const push = screen.getByRole("button", { name: "Push" })
    const history = screen.getByRole("button", { name: "History" })
    const publish = screen.getByRole("button", { name: "Publish" })
    for (const [before, after] of [
      [toggle, discard],
      [discard, push],
      [push, history],
      [history, publish],
    ]) {
      expect(
        before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
    }
    await userEvent.click(publish)
    expect(actions.submit).toHaveBeenCalledTimes(1)
    await userEvent.click(discard)
    expect(actions.discard).toHaveBeenCalledTimes(1)
    expect(mockUpdateAgentPreset).not.toHaveBeenCalled()
  })

  it("disables Publish for a clean preset and ignores clicks", async () => {
    const submit = jest.fn()
    mockUseDetailContext.mockReturnValue({
      actions: {
        workspaceId: "workspace-1",
        presetId: "preset-1",
        currentVersionId: "version-1",
        getDraftPayload: () => null,
        isSaving: false,
        canSubmit: false,
        submitLabel: "Save changes",
        submit,
        isDirty: false,
        cutsVersion: false,
        discard: jest.fn(),
      },
    })
    render(
      <TooltipProvider>
        <AgentPresetDetailHeaderActions />
      </TooltipProvider>
    )
    expect(
      screen.queryByRole("button", { name: "Discard" })
    ).not.toBeInTheDocument()
    const publish = screen.getByRole("button", { name: "Publish" })
    expect(publish).toHaveAttribute("aria-disabled", "true")
    await userEvent.click(publish)
    expect(submit).not.toHaveBeenCalled()
  })

  it.each([null, { actions: null }])(
    "keeps Push available before preset form registration: %p",
    (context) => {
      mockUseDetailContext.mockReturnValue(context)
      render(
        <AgentPresetDetailHeaderActions
          syncActions={<button type="button">Push</button>}
        />
      )
      expect(screen.getByRole("button", { name: "Push" })).toBeInTheDocument()
      expect(screen.queryByRole("switch")).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Publish" })
      ).not.toBeInTheDocument()
    }
  )
})
