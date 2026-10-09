import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { type AgentPresetReadMinimal, agentFoldersGetFolder } from "@/client"
import { AgentsDashboard } from "@/components/agents/agents-dashboard"
import { OpenAIIcon } from "@/components/icons"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockHasEntitlement = jest.fn<boolean, [string]>(() => false)
const mockSearchParams = { current: new URLSearchParams() }

const mockUseAgentPresets = jest.fn()
const mockUseAgentDirectoryItems = jest.fn()
const mockUseAgentTagCatalog = jest.fn()
const mockUseAgentFolders = jest.fn()
const mockUseWorkspaceDetails = jest.fn()
const mockUseAgentPreset = jest.fn()
const mockSetDefaultAgent = jest.fn()
const mockUpdateAgentPreset = jest.fn()
const mockUseScopeCheck = jest.fn<boolean, [string]>(() => true)
const mockPush = jest.fn()
const mockUseListMcpIntegrations = jest.fn()

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentFoldersGetFolder: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  ...jest.requireActual("@/lib/hooks"),
  useListMcpIntegrations: (...args: unknown[]) =>
    mockUseListMcpIntegrations(...args),
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceDetails: () => mockUseWorkspaceDetails(),
}))

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
  useSearchParams: () => mockSearchParams.current,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => mockHasEntitlement(key),
    isLoading: false,
    hasEntitlementData: true,
  }),
}))

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: (...args: unknown[]) => mockUseAgentPresets(...args),
  useAgentDirectoryItems: (...args: unknown[]) =>
    mockUseAgentDirectoryItems(...args),
  useAgentTagCatalog: (...args: unknown[]) => mockUseAgentTagCatalog(...args),
  useAgentFolders: (...args: unknown[]) => mockUseAgentFolders(...args),
  useAgentPreset: (...args: unknown[]) => mockUseAgentPreset(...args),
  useSetDefaultAgent: () => ({
    setDefaultAgent: mockSetDefaultAgent,
    isSettingDefaultAgent: false,
  }),
  useUpdateAgentPreset: () => ({
    updateAgentPreset: mockUpdateAgentPreset,
    updateAgentPresetIsPending: false,
  }),
  useCreateAgentPreset: () => ({
    createAgentPreset: jest.fn(),
    createAgentPresetIsPending: false,
  }),
  useDeleteAgentPreset: () => ({
    deleteAgentPreset: jest.fn(),
    deleteAgentPresetIsPending: false,
  }),
  useMoveAgentPreset: () => ({
    moveAgentPreset: jest.fn(),
    moveAgentPresetIsPending: false,
  }),
}))

const PRESET = {
  id: "preset-1",
  name: "Legacy preset",
  slug: "legacy-preset",
  description: null,
  use_in_chat: false,
  model_provider: "openai",
  model_name: "gpt-test",
  tool_summary: { tool_count: 0, namespaces: [], mcp_slugs: [] },
  folder_id: "folder-1",
  tags: [{ id: "tag-1", name: "legacy", ref: "legacy", color: "#000" }],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AgentsDashboard />
    </QueryClientProvider>
  )
}

function lastOptions(mock: jest.Mock): { enabled?: boolean } | undefined {
  const call = mock.mock.calls.at(-1)
  return call?.at(-1) as { enabled?: boolean } | undefined
}

describe("AgentsDashboard entitlement split", () => {
  beforeEach(() => {
    mockUseWorkspaceDetails.mockReturnValue({ workspace: { settings: {} } })
    mockUseAgentPreset.mockReturnValue({ preset: PRESET })
    mockSetDefaultAgent.mockClear()
    mockUpdateAgentPreset.mockReset()
    mockUpdateAgentPreset.mockResolvedValue(PRESET)
    mockUseScopeCheck.mockReset()
    mockUseScopeCheck.mockReturnValue(true)
    mockPush.mockClear()
    jest.mocked(agentFoldersGetFolder).mockReset()
    mockHasEntitlement.mockReset()
    mockHasEntitlement.mockReturnValue(false)
    mockSearchParams.current = new URLSearchParams()
    mockUseAgentPresets.mockReset()
    mockUseAgentPresets.mockReturnValue({
      presets: [PRESET],
      presetsIsLoading: false,
      presetsError: null,
    })
    mockUseAgentDirectoryItems.mockReset()
    mockUseAgentDirectoryItems.mockReturnValue({
      directoryItems: [],
      directoryItemsIsLoading: false,
      directoryItemsError: null,
    })
    mockUseAgentTagCatalog.mockReset()
    mockUseAgentTagCatalog.mockReturnValue({
      agentTags: [],
      agentTagsIsLoading: false,
    })
    mockUseAgentFolders.mockReset()
    mockUseAgentFolders.mockReturnValue({
      folders: [],
      foldersIsLoading: false,
    })
    mockUseListMcpIntegrations.mockReset()
    mockUseListMcpIntegrations.mockReturnValue({ mcpIntegrations: undefined })
  })

  describe.each(["list", "folders"] as const)("%s row details", (view) => {
    function renderPreset(summary: AgentPresetReadMinimal["tool_summary"]) {
      mockHasEntitlement.mockReturnValue(true)
      mockSearchParams.current = new URLSearchParams(`view=${view}`)
      const preset = { ...PRESET, tool_summary: summary }
      mockUseAgentPresets.mockReturnValue({
        presets: [preset],
        presetsIsLoading: false,
        presetsError: null,
      })
      mockUseAgentDirectoryItems.mockReturnValue({
        directoryItems: [{ ...preset, type: "preset" }],
        directoryItemsIsLoading: false,
        directoryItemsError: null,
      })
      return renderDashboard()
    }

    it("shows unique sources, MCP integrations, and a model with its provider icon", async () => {
      renderPreset({
        tool_count: 4,
        namespaces: [
          "tools.slack",
          "tools.slack_sdk",
          "tools.slack_blocks",
          "tools.jira",
        ],
        mcp_slugs: ["runreveal"],
      })
      const sources = within(screen.getByTestId("tool-sources"))
      const slack = sources.getByRole("img", {
        name: "tools.slack, tools.slack_sdk, tools.slack_blocks",
      })
      expect(
        sources.queryByLabelText("tools.slack_sdk")
      ).not.toBeInTheDocument()
      expect(
        sources.queryByLabelText("tools.slack_blocks")
      ).not.toBeInTheDocument()
      expect(sources.getByLabelText("tools.jira")).toBeInTheDocument()
      expect(sources.getByRole("separator")).toHaveAttribute(
        "aria-orientation",
        "vertical"
      )
      const mcp = sources.getByLabelText("runreveal")
      expect(mcp.querySelector("svg")).toBeInTheDocument()
      expect(slack.firstElementChild).toHaveClass("size-5", "rounded", "border")
      const model = screen.getByText(PRESET.model_name)
      const expectedIcon = render(<OpenAIIcon />)
      const expectedPath = expectedIcon.container
        .querySelector("path")
        ?.getAttribute("d")
      expect(expectedPath).toBeTruthy()
      expect(model.querySelector("svg path")).toHaveAttribute("d", expectedPath)
      expectedIcon.unmount()
      expect(model.firstElementChild).toHaveClass("rounded-none")
      expect(model.firstElementChild).toHaveClass(
        "size-3",
        "bg-transparent",
        "p-0"
      )
      expect(screen.queryByText(PRESET.model_provider)).not.toBeInTheDocument()

      await userEvent.hover(sources.getByText("4 tools + 1 MCP"))
      await new Promise((resolve) => setTimeout(resolve, 1000))
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument()
    })

    it.each([
      ["registry only", 3, [], "3 tools"],
      ["registry and remote MCP", 3, ["remote"], "7 tools"],
      ["only remote MCP", 0, ["remote"], "4 tools"],
      ["a single remote MCP tool", 0, ["single"], "1 tool"],
      ["only stdio MCP", 0, ["stdio-a"], "1 stdio MCP"],
      ["registry and stdio MCP", 1, ["stdio-a"], "1 tool + 1 stdio MCP"],
      ["several stdio MCP", 1, ["stdio-a", "stdio-b"], "1 tool + 2 stdio MCP"],
      ["remote MCP without tools", 3, ["empty"], "3 tools + 1 MCP"],
      ["remote MCP never discovered", 0, ["undiscovered"], "1 MCP"],
      ["an integration that is not loaded", 2, ["missing"], "2 tools + 1 MCP"],
      [
        "every kind at once",
        1,
        ["remote", "empty", "stdio-a"],
        "5 tools + 1 MCP + 1 stdio MCP",
      ],
    ])("counts tools for %s", (_name, toolCount, mcpSlugs, label) => {
      const tool = (name: string, extra = {}) => ({ name, ...extra })
      mockUseListMcpIntegrations.mockReturnValue({
        mcpIntegrations: [
          {
            slug: "remote",
            server_type: "http",
            tools: [
              tool("a"),
              tool("b"),
              tool("c"),
              tool("d"),
              tool("off", { enabled: false }),
              tool("gone", { status: "missing" }),
            ],
          },
          { slug: "single", server_type: "http", tools: [tool("a")] },
          { slug: "empty", server_type: "http", tools: [] },
          { slug: "undiscovered", server_type: "http", tools: null },
          { slug: "stdio-a", server_type: "stdio", tools: null },
          { slug: "stdio-b", server_type: "stdio", tools: [tool("a")] },
        ],
      })
      renderPreset({
        tool_count: toolCount,
        namespaces: toolCount > 0 ? ["tools.slack"] : [],
        mcp_slugs: mcpSlugs,
      })
      const sources = within(screen.getByTestId("tool-sources"))
      expect(sources.getByText(label)).toBeInTheDocument()
      expect(sources.queryAllByRole("separator")).toHaveLength(
        toolCount > 0 && mcpSlugs.length > 0 ? 1 : 0
      )
    })

    it("loads MCP integrations only when a preset uses one", () => {
      const { unmount } = renderPreset({
        tool_count: 1,
        namespaces: ["tools.slack"],
        mcp_slugs: [],
      })
      expect(lastOptions(mockUseListMcpIntegrations)?.enabled).toBe(false)
      unmount()
      renderPreset({ tool_count: 1, namespaces: [], mcp_slugs: ["remote"] })
      expect(lastOptions(mockUseListMcpIntegrations)?.enabled).toBe(true)
    })

    it("does not load MCP integrations without the integration read scope", () => {
      mockUseScopeCheck.mockImplementation(
        (scope) => scope !== "integration:read"
      )
      renderPreset({ tool_count: 1, namespaces: [], mcp_slugs: ["remote"] })
      expect(lastOptions(mockUseListMcpIntegrations)?.enabled).toBe(false)
    })

    it("shows the provider slug in the model badge tooltip", async () => {
      renderPreset(undefined)
      await userEvent.hover(screen.getByText(PRESET.model_name))
      expect(
        await screen.findByRole("tooltip", {}, { timeout: 2000 })
      ).toHaveTextContent(PRESET.model_provider)
    })

    it.each(["tools.slack", "runreveal"])(
      "names the %s source in a tooltip",
      async (source) => {
        renderPreset({
          tool_count: 1,
          namespaces: ["tools.slack"],
          mcp_slugs: ["runreveal"],
        })
        await userEvent.hover(screen.getByLabelText(source))
        expect(
          await screen.findByRole("tooltip", {}, { timeout: 2000 })
        ).toHaveTextContent(source)
      }
    )

    it("omits the tool area for an empty summary", () => {
      renderPreset({ tool_count: 0, namespaces: [], mcp_slugs: [] })
      expect(screen.queryByTestId("tool-sources")).not.toBeInTheDocument()
      expect(screen.queryByText("0 tools")).not.toBeInTheDocument()
      expect(screen.getByText(PRESET.model_name)).toBeInTheDocument()
    })

    it("uses the singular count and omits the divider without MCP integrations", () => {
      renderPreset({
        tool_count: 1,
        namespaces: ["tools.slack"],
        mcp_slugs: [],
      })
      const sources = within(screen.getByTestId("tool-sources"))
      expect(sources.getByText("1 tool")).toBeInTheDocument()
      expect(sources.queryByRole("separator")).not.toBeInTheDocument()
    })

    it("shows MCP-only presets without a registry tool count or divider", () => {
      renderPreset({ tool_count: 0, namespaces: [], mcp_slugs: ["runreveal"] })
      const sources = within(screen.getByTestId("tool-sources"))
      expect(sources.getByLabelText("runreveal")).toBeInTheDocument()
      expect(sources.queryByText(/0 tools/)).not.toBeInTheDocument()
      expect(sources.queryByRole("separator")).not.toBeInTheDocument()
    })

    it.each([
      ["custom.first", "custom.second"],
      ["tools.gitlab", "tools.misp", "tools.opensearch"],
      ["ai", "ai.agent", "ai.skill", "custom.first"],
    ])("preserves namespaces sharing one icon: %s", async (...namespaces) => {
      renderPreset({
        tool_count: 2,
        namespaces,
        mcp_slugs: [],
      })
      const tile = screen.getByRole("img", {
        name: namespaces.join(", "),
      })
      expect(
        within(screen.getByTestId("tool-sources")).getAllByRole("img")
      ).toHaveLength(1)
      expect(tile.closest("button")?.textContent).toContain("2 tools")
      expect(tile.closest("button")).toHaveAccessibleName(
        expect.stringContaining(namespaces.join(", "))
      )
      await userEvent.click(tile)
      expect(mockPush).toHaveBeenCalledWith(expect.stringContaining(PRESET.id))
    })

    it("handles an undefined summary", () => {
      renderPreset(undefined)
      expect(screen.queryByTestId("tool-sources")).not.toBeInTheDocument()
      expect(screen.getByText(PRESET.model_name)).toBeInTheDocument()
    })

    it.each([8, 9])("caps exactly %i distinct sources", async (count) => {
      const namespaces = [
        "tools.slack",
        "tools.jira",
        "tools.github",
        "tools.notion",
        "tools.linear",
        "tools.sentry",
        "tools.exa",
        "tools.wiz",
        "tools.datadog",
      ].slice(0, count)
      renderPreset({ tool_count: count, namespaces, mcp_slugs: [] })
      if (count === 8) {
        expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument()
      } else {
        await userEvent.hover(screen.getByText("+1"))
        expect(
          await screen.findByRole("tooltip", {}, { timeout: 2000 })
        ).toHaveTextContent("tools.datadog")
      }
    })

    it("caps MCP integrations at eight without merging shared icons", async () => {
      const slugs = Array.from(
        { length: 9 },
        (_, index) => `synthetic-mcp-${index}`
      )
      renderPreset({ tool_count: 0, namespaces: [], mcp_slugs: slugs })
      const sources = within(screen.getByTestId("tool-sources"))
      expect(sources.getAllByRole("img")).toHaveLength(8)
      expect(sources.queryByLabelText(slugs[8])).not.toBeInTheDocument()
      await userEvent.hover(sources.getByText("+1"))
      expect(
        await screen.findByRole("tooltip", {}, { timeout: 2000 })
      ).toHaveTextContent(slugs[8])
    })

    it("caps registry tiles at eight after deduplication", () => {
      renderPreset({
        tool_count: 12,
        namespaces: [
          "tools.slack",
          "tools.slack_sdk",
          "tools.slack_blocks",
          "tools.jira",
          "tools.github",
          "tools.notion",
          "tools.linear",
          "tools.sentry",
          "tools.exa",
          "tools.wiz",
          "tools.datadog",
          "tools.snowflake",
        ],
        mcp_slugs: ["runreveal"],
      })
      const sources = within(screen.getByTestId("tool-sources"))
      expect(sources.getByText("+2")).toBeInTheDocument()
      expect(sources.getByLabelText("tools.wiz")).toBeInTheDocument()
      expect(sources.queryByLabelText("tools.datadog")).not.toBeInTheDocument()
      expect(
        sources.queryByLabelText("tools.snowflake")
      ).not.toBeInTheDocument()
      expect(sources.getByLabelText("runreveal")).toBeInTheDocument()
    })
  })

  it("renders a flat catalog without folder/tag queries when unentitled", () => {
    mockSearchParams.current = new URLSearchParams("view=folders&path=/legacy/")

    renderDashboard()

    expect(lastOptions(mockUseAgentPresets)?.enabled).toBe(true)
    expect(lastOptions(mockUseAgentDirectoryItems)?.enabled).toBe(false)
    expect(lastOptions(mockUseAgentTagCatalog)?.enabled).toBe(false)
    expect(lastOptions(mockUseAgentFolders)?.enabled).toBe(false)

    expect(screen.getByText("Legacy preset")).toBeInTheDocument()
    expect(screen.queryByText("legacy")).not.toBeInTheDocument()
    expect(screen.queryByText("View")).not.toBeInTheDocument()
  })

  it("uses the folder directory and tag catalog when entitled", () => {
    mockHasEntitlement.mockImplementation((key) => key === "agent_addons")
    mockSearchParams.current = new URLSearchParams("view=folders")

    renderDashboard()

    expect(lastOptions(mockUseAgentPresets)?.enabled).toBe(false)
    expect(lastOptions(mockUseAgentDirectoryItems)?.enabled).toBe(true)
    expect(lastOptions(mockUseAgentTagCatalog)?.enabled).toBe(true)
    expect(screen.getByText("View")).toBeInTheDocument()
  })

  it("sets the default through the context menu without opening the agent", async () => {
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Make default" })
    )
    expect(mockSetDefaultAgent).toHaveBeenCalledWith(PRESET.id)
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("hides Make default without workspace update permission", () => {
    mockUseScopeCheck.mockImplementation(
      (scope) => scope !== "workspace:update"
    )
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    expect(
      screen.queryByRole("menuitem", { name: "Make default" })
    ).not.toBeInTheDocument()
  })

  it.each([false, true])(
    "toggles chat visibility from %s without navigation",
    async (enabled) => {
      mockUseAgentPresets.mockReturnValue({
        presets: [{ ...PRESET, use_in_chat: enabled }],
        presetsIsLoading: false,
        presetsError: null,
      })
      renderDashboard()
      fireEvent.contextMenu(screen.getByText(PRESET.name))
      const toggle = await screen.findByRole("menuitemcheckbox", {
        name: "Use in chat",
      })
      expect(toggle).toHaveAttribute("aria-checked", String(enabled))
      await userEvent.click(toggle)
      expect(mockUpdateAgentPreset).toHaveBeenCalledWith({
        presetId: PRESET.id,
        use_in_chat: !enabled,
      })
      expect(mockPush).not.toHaveBeenCalled()
    }
  )

  it("hides the chat toggle without agent update permission", () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    expect(
      screen.queryByRole("menuitemcheckbox", { name: "Use in chat" })
    ).not.toBeInTheDocument()
  })

  it("reflects chat visibility in the folder view", async () => {
    mockHasEntitlement.mockReturnValue(true)
    mockSearchParams.current = new URLSearchParams("view=folders")
    mockUseAgentDirectoryItems.mockReturnValue({
      directoryItems: [{ ...PRESET, type: "preset", use_in_chat: true }],
      directoryItemsIsLoading: false,
      directoryItemsError: null,
    })
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    const toggle = await screen.findByRole("menuitemcheckbox", {
      name: "Use in chat",
    })
    expect(toggle).toHaveAttribute("aria-checked", "true")
    await userEvent.click(toggle)
    expect(mockUpdateAgentPreset).toHaveBeenCalledWith({
      presetId: PRESET.id,
      use_in_chat: false,
    })
  })

  it("marks the default row and links the header badge to the preset", () => {
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: { default_agent_preset_id: PRESET.id },
    })
    renderDashboard()
    expect(screen.getByText("Default")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: `Default agent: ${PRESET.name}` })
    ).toHaveAttribute("href", `/workspaces/workspace-1/agents/${PRESET.id}`)
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    expect(
      screen.getByRole("menuitem", { name: "Make default" })
    ).toHaveAttribute("data-disabled")
    expect(agentFoldersGetFolder).not.toHaveBeenCalled()
  })

  it("previews the default outside the current folder, including description and folder", async () => {
    mockHasEntitlement.mockReturnValue(true)
    mockSearchParams.current = new URLSearchParams("view=folders")
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: { default_agent_preset_id: PRESET.id },
    })
    mockUseAgentPreset.mockReturnValue({
      preset: { ...PRESET, description: "Synthetic default agent description" },
    })
    jest.mocked(agentFoldersGetFolder).mockResolvedValue({
      id: "folder-1",
      name: "Nested",
      path: "/Parent/Nested/",
      workspace_id: "workspace-1",
      created_at: PRESET.created_at,
      updated_at: PRESET.updated_at,
    })
    renderDashboard()
    await userEvent.hover(
      screen.getByRole("link", { name: `Default agent: ${PRESET.name}` })
    )
    await waitFor(
      () => {
        expect(
          screen.getByText("Synthetic default agent description")
        ).toBeInTheDocument()
        expect(screen.getByText("/Parent/Nested/")).toBeInTheDocument()
        expect(
          screen.queryByText("Workspace default agent")
        ).not.toBeInTheDocument()
      },
      { timeout: 2000 }
    )
  })

  it("does not render a header badge when no default is configured", () => {
    renderDashboard()
    expect(
      screen.queryByRole("link", { name: /Default agent:/ })
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Default")).not.toBeInTheDocument()
  })

  it("hides stale or deleted default details rather than linking to a missing preset", () => {
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: { default_agent_preset_id: PRESET.id },
    })
    mockUseAgentPreset.mockReturnValue({
      preset: undefined,
      presetError: new Error("Not found"),
    })
    renderDashboard()
    expect(
      screen.queryByRole("link", { name: /Default agent:/ })
    ).not.toBeInTheDocument()
  })
})
