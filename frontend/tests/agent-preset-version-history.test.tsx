import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { AgentPresetCreate, AgentPresetVersionRead } from "@/client"
import { AgentPresetVersionHistory } from "@/components/agents/agent-preset-version-history"
import { InlineDiffView } from "@/components/diff/inline-diff-view"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentPreset,
  useAgentPresetVersion,
  useAgentPresetVersions,
  useRestoreAgentPresetVersion,
} from "@/hooks/use-agent-presets"
import { useSkills } from "@/hooks/use-skills"

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPreset: jest.fn(),
  useAgentPresetVersion: jest.fn(),
  useAgentPresetVersions: jest.fn(),
  useRestoreAgentPresetVersion: jest.fn(),
}))

jest.mock("@/hooks/use-skills", () => ({
  useSkills: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

// The real InlineDiffView renders prose/unified diff internals that this suite
// does not care about; the mock records its props so the tests can pin the
// diff direction on the exact values the adapter passes down.
jest.mock("@/components/diff/inline-diff-view", () => ({
  InlineDiffView: jest.fn(
    ({ path }: { path: string }): React.ReactNode => (
      <div data-testid="inline-diff-view">{path}</div>
    )
  ),
}))

const mockUseAgentPreset = useAgentPreset as jest.Mock
const mockUseAgentPresetVersion = useAgentPresetVersion as jest.Mock
const mockUseAgentPresetVersions = useAgentPresetVersions as jest.Mock
const mockUseRestoreAgentPresetVersion =
  useRestoreAgentPresetVersion as jest.Mock
const mockUseSkills = useSkills as jest.Mock
const mockInlineDiffView = InlineDiffView as jest.Mock
const mockToast = toast as jest.Mock

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      value: () => false,
    })
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      value: () => undefined,
    })
  }
})

const WORKSPACE_ID = "ws-1"
const PRESET_ID = "preset-1"
const CURRENT_VERSION_ID = "ver-2"
const SELECTED_VERSION_ID = "ver-1"

/** Execution fields shared verbatim by the saved version and matching drafts. */
const EXECUTION_FIELDS = {
  instructions: "version instructions",
  model_name: "test-model",
  model_provider: "test-provider",
  base_url: null,
  catalog_id: null,
  output_type: null,
  actions: ["tools.example.list_items"],
  namespaces: [],
  tool_approvals: {},
  mcp_integrations: [],
  retries: 3,
  enable_thinking: false,
  enable_internet_access: false,
}

const VERSION_READ: AgentPresetVersionRead = {
  ...EXECUTION_FIELDS,
  id: SELECTED_VERSION_ID,
  preset_id: PRESET_ID,
  workspace_id: WORKSPACE_ID,
  version: 1,
  skills: [],
  restore_skills: [],
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
}

const VERSIONS_MINIMAL = [
  {
    id: CURRENT_VERSION_ID,
    preset_id: PRESET_ID,
    workspace_id: WORKSPACE_ID,
    version: 2,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
  {
    id: SELECTED_VERSION_ID,
    preset_id: PRESET_ID,
    workspace_id: WORKSPACE_ID,
    version: 1,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  },
]

function buildDraftPayload(
  overrides: Partial<AgentPresetCreate> = {}
): AgentPresetCreate {
  return {
    ...EXECUTION_FIELDS,
    name: "Triage agent",
    slug: "triage-agent",
    description: "Draft description",
    skills: [],
    ...overrides,
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUseAgentPreset.mockReturnValue({
    preset: { name: "Triage agent" },
    presetIsLoading: false,
    presetError: null,
    refetchPreset: jest.fn(),
  })
  mockUseAgentPresetVersions.mockReturnValue({
    versions: VERSIONS_MINIMAL,
    versionsIsLoading: false,
    versionsError: null,
    refetchVersions: jest.fn(),
  })
  mockUseAgentPresetVersion.mockReturnValue({
    presetVersion: VERSION_READ,
    presetVersionIsLoading: false,
    presetVersionError: null,
    refetchPresetVersion: jest.fn(),
  })
  mockUseRestoreAgentPresetVersion.mockReturnValue({
    restoreAgentPresetVersion: jest.fn().mockResolvedValue(undefined),
    restoreAgentPresetVersionIsPending: false,
    restoreAgentPresetVersionError: null,
  })
  mockUseSkills.mockReturnValue({
    skills: [],
    skillsLoading: false,
    skillsError: null,
  })
})

function renderHistory(getDraftPayload: () => AgentPresetCreate | null) {
  render(
    <TooltipProvider>
      <AgentPresetVersionHistory
        workspaceId={WORKSPACE_ID}
        presetId={PRESET_ID}
        currentVersionId={CURRENT_VERSION_ID}
        getDraftPayload={getDraftPayload}
      />
    </TooltipProvider>
  )
}

async function openVersionOneDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Versions" }))
  await user.click(screen.getByText("Version 1"))
  expect(screen.getByRole("alertdialog")).toBeInTheDocument()
}

describe("AgentPresetVersionHistory", () => {
  it("shows exactly the two virtual files with their status badges", async () => {
    const user = userEvent.setup()
    // Instructions and actions both differ, so both files are modified.
    renderHistory(() =>
      buildDraftPayload({
        instructions: "draft instructions",
        actions: ["tools.example.create_item"],
      })
    )

    await openVersionOneDialog(user)

    const tree = screen.getByRole("tree")
    const items = within(tree).getAllByRole("treeitem")
    expect(items).toHaveLength(2)
    // instructions.md is pinned above config.yaml despite sorting after it.
    expect(items[0]).toHaveTextContent("instructions.md")
    expect(items[0]).toHaveTextContent("Modified")
    expect(items[1]).toHaveTextContent("config.yaml")
    expect(items[1]).toHaveTextContent("Modified")
    expect(within(tree).queryByText("Added")).not.toBeInTheDocument()
    expect(within(tree).queryByText("Removed")).not.toBeInTheDocument()
  })

  it("diffs with the draft as oldValue and the version as newValue", async () => {
    const user = userEvent.setup()
    // Only the instructions differ, so instructions.md is auto-selected as the
    // first changed file.
    renderHistory(() =>
      buildDraftPayload({ instructions: "draft instructions" })
    )

    await openVersionOneDialog(user)

    expect(mockInlineDiffView).toHaveBeenCalled()
    const lastCall = mockInlineDiffView.mock.calls.at(-1)
    // Direction is fixed: oldValue is the CURRENT DRAFT, newValue is the
    // SELECTED VERSION. Highlighted text is what restoring brings back;
    // struck-through text is draft content the restore would lose. If this
    // assertion fails, the diff reads backwards — do not swap the expectation.
    expect(lastCall?.[0]).toMatchObject({
      path: "instructions.md",
      oldValue: "draft instructions\n",
      newValue: "version instructions\n",
    })
  })

  it("shows config.yaml as unchanged for metadata-only differences", async () => {
    const user = userEvent.setup()
    // Name, slug, and description differ, but no execution field does. Those
    // metadata fields are not versioned, so the diff must show nothing.
    renderHistory(() =>
      buildDraftPayload({
        name: "Renamed agent",
        slug: "renamed-agent",
        description: "A different description",
      })
    )

    await openVersionOneDialog(user)

    const tree = screen.getByRole("tree")
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(2)
    expect(within(tree).queryByText("Modified")).not.toBeInTheDocument()
    expect(within(tree).queryByText("Added")).not.toBeInTheDocument()
    expect(within(tree).queryByText("Removed")).not.toBeInTheDocument()
  })

  it("previews current Skill heads instead of historical version pins", async () => {
    const skillId = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    // The immutable version records v5, but restore follows the Skill's current
    // v2 head. The preview must therefore match the v2 draft binding.
    mockUseAgentPreset.mockReturnValue({
      preset: {
        name: "Triage agent",
        skills: [
          {
            skill_id: skillId,
            skill_version_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            skill_name: "alpha-skill",
            skill_version: 2,
          },
        ],
      },
      presetIsLoading: false,
      presetError: null,
      refetchPreset: jest.fn(),
    })
    mockUseAgentPresetVersion.mockReturnValue({
      presetVersion: {
        ...VERSION_READ,
        skills: [
          {
            skill_id: skillId,
            skill_version_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
            skill_name: "alpha-skill",
            skill_version: 5,
          },
        ],
        restore_skills: [
          {
            skill_id: skillId,
            skill_version_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            skill_name: "alpha-skill",
            skill_version: 2,
          },
        ],
      },
      presetVersionIsLoading: false,
      presetVersionError: null,
      refetchPresetVersion: jest.fn(),
    })
    mockUseSkills.mockReturnValue({
      skills: [{ id: skillId, name: "alpha-skill" }],
      skillsLoading: false,
      skillsError: null,
    })
    const user = userEvent.setup()
    renderHistory(() => buildDraftPayload({ skills: [{ skill_id: skillId }] }))

    await openVersionOneDialog(user)

    const tree = screen.getByRole("tree")
    const items = within(tree).getAllByRole("treeitem")
    expect(items[0]).toHaveTextContent("instructions.md")
    expect(items[0]).not.toHaveTextContent("Modified")
    expect(items[1]).toHaveTextContent("config.yaml")
    expect(items[1]).not.toHaveTextContent("Modified")
  })

  it("shows the load-error state and toasts when versions fail to load", async () => {
    mockUseAgentPresetVersions.mockReturnValue({
      versions: undefined,
      versionsIsLoading: false,
      versionsError: { status: 500 },
      refetchVersions: jest.fn(),
    })
    const user = userEvent.setup()
    renderHistory(() => buildDraftPayload())

    await user.click(screen.getByRole("button", { name: "Versions" }))

    expect(
      screen.getByText("Couldn't load version history.")
    ).toBeInTheDocument()
    expect(screen.queryByText("No versions yet.")).not.toBeInTheDocument()
    // Once per failure, despite the re-renders from opening the dropdown.
    expect(mockToast).toHaveBeenCalledTimes(1)
    expect(mockToast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Couldn't load version history" })
    )
  })

  it("shows the empty state without a toast on a successful empty result", async () => {
    mockUseAgentPresetVersions.mockReturnValue({
      versions: [],
      versionsIsLoading: false,
      versionsError: null,
      refetchVersions: jest.fn(),
    })
    const user = userEvent.setup()
    renderHistory(() => buildDraftPayload())

    await user.click(screen.getByRole("button", { name: "Versions" }))

    expect(screen.getByText("No versions yet.")).toBeInTheDocument()
    expect(
      screen.queryByText("Couldn't load version history.")
    ).not.toBeInTheDocument()
    expect(mockToast).not.toHaveBeenCalled()
  })

  it("shows the form-error notice and disables restore when the draft cannot serialize", async () => {
    const user = userEvent.setup()
    renderHistory(() => null)

    await openVersionOneDialog(user)

    expect(
      screen.getByText("Fix form errors before comparing versions.")
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Restore version" })
      ).toBeDisabled()
    })
    // The whole diff body is replaced by the notice.
    expect(screen.queryByRole("tree")).not.toBeInTheDocument()
  })
})
