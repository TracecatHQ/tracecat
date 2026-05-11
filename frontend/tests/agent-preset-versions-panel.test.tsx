import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { FormEvent } from "react"
import type { AgentPresetRead, AgentPresetVersionReadMinimal } from "@/client"
import { AgentPresetVersionsPanel } from "@/components/agents/agent-preset-versions-panel"
import {
  useAgentPresetVersions,
  useCompareAgentPresetVersions,
  useRestoreAgentPresetVersion,
} from "@/hooks/use-agent-presets"

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresetVersions: jest.fn(),
  useCompareAgentPresetVersions: jest.fn(),
  useRestoreAgentPresetVersion: jest.fn(),
}))

jest.mock("@/lib/event-history", () => ({
  getRelativeTime: () => "just now",
}))

jest.mock("react-diff-viewer-continued", () => {
  const React = require("react")

  function MockReactDiffViewer({
    oldValue,
    newValue,
    leftTitle,
    rightTitle,
  }: {
    oldValue: string
    newValue: string
    leftTitle: string
    rightTitle: string
  }) {
    return React.createElement(
      "div",
      { "data-testid": "mock-react-diff-viewer" },
      React.createElement("div", null, leftTitle),
      React.createElement("div", null, rightTitle),
      React.createElement("pre", null, oldValue),
      React.createElement("pre", null, newValue)
    )
  }

  return {
    __esModule: true,
    default: MockReactDiffViewer,
    DiffMethod: {
      CHARS: "CHARS",
      WORDS: "WORDS",
      WORDS_WITH_SPACE: "WORDS_WITH_SPACE",
      LINES: "LINES",
    },
  }
})

const mockUseAgentPresetVersions =
  useAgentPresetVersions as jest.MockedFunction<typeof useAgentPresetVersions>
const mockUseCompareAgentPresetVersions =
  useCompareAgentPresetVersions as jest.MockedFunction<
    typeof useCompareAgentPresetVersions
  >
const mockUseRestoreAgentPresetVersion =
  useRestoreAgentPresetVersion as jest.MockedFunction<
    typeof useRestoreAgentPresetVersion
  >

const presetFixture: AgentPresetRead = {
  id: "preset-1",
  workspace_id: "workspace-1",
  name: "Versioned QA agent",
  slug: "versioned-qa-agent",
  description: "Used to verify restore behavior.",
  instructions: "v2 prompt",
  model_name: "gpt-4o-mini",
  model_provider: "openai",
  base_url: null,
  output_type: null,
  actions: ["core.http_request"],
  namespaces: null,
  tool_approvals: null,
  mcp_integrations: null,
  retries: 3,
  enable_internet_access: false,
  current_version_id: "version-2",
  created_at: "2026-03-07T00:00:00.000Z",
  updated_at: "2026-03-07T00:00:00.000Z",
}

const versionsFixture: AgentPresetVersionReadMinimal[] = [
  {
    id: "version-2",
    preset_id: "preset-1",
    workspace_id: "workspace-1",
    version: 2,
    created_at: "2026-03-07T00:00:02.000Z",
    updated_at: "2026-03-07T00:00:02.000Z",
  },
  {
    id: "version-1",
    preset_id: "preset-1",
    workspace_id: "workspace-1",
    version: 1,
    created_at: "2026-03-07T00:00:01.000Z",
    updated_at: "2026-03-07T00:00:01.000Z",
  },
]

describe("AgentPresetVersionsPanel", () => {
  beforeEach(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }

    mockUseAgentPresetVersions.mockReturnValue({
      versions: versionsFixture,
      versionsIsLoading: false,
      versionsError: null,
      refetchVersions: jest.fn(),
    })
    mockUseCompareAgentPresetVersions.mockReturnValue({
      diff: undefined,
      diffIsLoading: false,
      diffError: null,
      refetchDiff: jest.fn(),
    })
  })

  afterEach(() => {
    jest.clearAllMocks()
  })

  it("restores a version without submitting the parent form", async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn((event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
    })
    const restoreAgentPresetVersion = jest.fn().mockResolvedValue(presetFixture)

    mockUseRestoreAgentPresetVersion.mockReturnValue({
      restoreAgentPresetVersion,
      restoreAgentPresetVersionIsPending: false,
      restoreAgentPresetVersionError: null,
    })

    render(
      <form onSubmit={onSubmit}>
        <AgentPresetVersionsPanel
          workspaceId="workspace-1"
          preset={presetFixture}
        />
      </form>
    )

    await user.click(screen.getByRole("button", { name: "Restore" }))

    expect(restoreAgentPresetVersion).toHaveBeenCalledWith({
      presetId: "preset-1",
      versionId: "version-1",
    })
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
