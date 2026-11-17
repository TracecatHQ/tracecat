import { renderHook } from "@testing-library/react"
import { useMentionSuggestions } from "@/hooks/use-mentions"

const mockUseAgentPresets = jest.fn()

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: (...args: unknown[]) => mockUseAgentPresets(...args),
}))
jest.mock("@/hooks/use-comment-workflows", () => ({
  useCommentWorkflows: () => ({ items: [], isLoading: false }),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: () => true,
    hasEntitlementData: true,
  }),
}))

describe("agent mention opt-in", () => {
  it("requests only chat-enabled agents and excludes disabled or legacy data", () => {
    mockUseAgentPresets.mockReturnValue({
      presets: [
        {
          id: "enabled",
          name: "Enabled agent",
          slug: "enabled",
          use_in_chat: true,
        },
        {
          id: "disabled",
          name: "Disabled agent",
          slug: "disabled",
          use_in_chat: false,
        },
        { id: "legacy", name: "Legacy agent", slug: "legacy" },
      ],
    })
    const { result, rerender } = renderHook(
      ({ query }) =>
        useMentionSuggestions({
          workspaceId: "workspace-1",
          activeMention: { kind: "agent", query },
          agents: { entitlements: [] },
        }),
      { initialProps: { query: "" } }
    )

    expect(mockUseAgentPresets).toHaveBeenCalledWith("workspace-1", {
      enabled: true,
      useInChat: true,
    })
    expect(result.current.sections[0].items.map((item) => item.id)).toEqual([
      "enabled",
    ])
    rerender({ query: "disabled" })
    expect(result.current.sections).toEqual([])
    rerender({ query: "enabled" })
    expect(result.current.sections[0].items.map((item) => item.id)).toEqual([
      "enabled",
    ])
  })

  it("removes an agent from suggestions when chat visibility is disabled", () => {
    mockUseAgentPresets.mockReturnValue({
      presets: [{ id: "agent", name: "Test", slug: "test", use_in_chat: true }],
    })
    const { result, rerender } = renderHook(() =>
      useMentionSuggestions({
        workspaceId: "workspace-1",
        activeMention: { kind: "agent", query: "" },
        agents: { entitlements: [] },
      })
    )
    expect(result.current.sections[0].items).toHaveLength(1)
    mockUseAgentPresets.mockReturnValue({
      presets: [
        { id: "agent", name: "Test", slug: "test", use_in_chat: false },
      ],
    })
    rerender()
    expect(result.current.sections).toEqual([])
  })
})
