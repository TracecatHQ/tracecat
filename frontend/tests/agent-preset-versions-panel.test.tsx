import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { FormEvent } from "react"
import type {
  AgentPresetRead,
  AgentPresetVersionDiff,
  AgentPresetVersionReadMinimal,
} from "@/client"
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

  it("renders skill attachment changes without child versions", async () => {
    const user = userEvent.setup()
    const diff: AgentPresetVersionDiff = {
      base_version_id: "version-1",
      base_version: 1,
      compare_version_id: "version-2",
      compare_version: 2,
      base_instructions: "v1 prompt",
      compare_instructions: "v2 prompt",
      skill_changes: [
        {
          skill_id: "skill-attached",
          skill_name: "incident-enrichment",
          change_type: "attached",
        },
        {
          skill_id: "skill-detached",
          skill_name: "legacy-enrichment",
          change_type: "detached",
        },
      ],
      total_changes: 2,
    }

    mockUseCompareAgentPresetVersions.mockReturnValue({
      diff,
      diffIsLoading: false,
      diffError: null,
      refetchDiff: jest.fn(),
    })
    mockUseRestoreAgentPresetVersion.mockReturnValue({
      restoreAgentPresetVersion: jest.fn(),
      restoreAgentPresetVersionIsPending: false,
      restoreAgentPresetVersionError: null,
    })

    render(
      <AgentPresetVersionsPanel
        workspaceId="workspace-1"
        preset={presetFixture}
      />
    )

    await user.click(screen.getAllByRole("button", { name: "Compare" })[0])

    expect(screen.getByText("incident-enrichment")).toBeInTheDocument()
    expect(screen.getByText("legacy-enrichment")).toBeInTheDocument()
    expect(screen.getByText("Attached")).toBeInTheDocument()
    expect(screen.getByText("Detached")).toBeInTheDocument()
    expect(screen.queryByText("Not attached")).not.toBeInTheDocument()
  })
})
