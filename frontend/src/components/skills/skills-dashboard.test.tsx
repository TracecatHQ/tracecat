import { render, screen } from "@testing-library/react"
import { SkillsDashboard } from "@/components/skills/skills-dashboard"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockHasEntitlement = jest.fn<boolean, [string]>(() => false)
const mockSearchParams = { current: new URLSearchParams() }
const mockUseSkills = jest.fn()
const mockUseSkillDirectoryItems = jest.fn()
const mockUseSkillTagCatalog = jest.fn()
const mockUseAgentPresets = jest.fn()

// No resource is managed by Tracecat in these tests.
jest.mock("@/hooks/use-managed-resources", () => ({
  useManagedResources: () => ({
    managedResources: [],
    detachedCopies: [],
    isLoading: false,
    isManaged: () => false,
    getManaged: () => undefined,
    getDetachedCopy: () => undefined,
  }),
}))
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => mockSearchParams.current,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
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
}))

jest.mock("@/hooks/use-skills", () => ({
  useSkills: (...args: unknown[]) => mockUseSkills(...args),
  useDeleteSkill: () => ({
    deleteSkill: jest.fn(),
    deleteSkillPending: false,
  }),
}))

jest.mock("@/hooks/use-skill-folders", () => ({
  useSkillDirectoryItems: (...args: unknown[]) =>
    mockUseSkillDirectoryItems(...args),
  useSkillFolders: () => ({
    folders: [],
    foldersIsLoading: false,
    createFolder: jest.fn(),
    updateFolder: jest.fn(),
    moveFolder: jest.fn(),
    deleteFolder: jest.fn(),
  }),
  useMoveSkill: () => ({
    moveSkill: jest.fn(),
    moveSkillIsPending: false,
  }),
}))

jest.mock("@/hooks/use-skill-tags", () => ({
  useSkillTagCatalog: (...args: unknown[]) => mockUseSkillTagCatalog(...args),
  useSkillTagMutations: () => ({
    addSkillTag: jest.fn(),
    removeSkillTag: jest.fn(),
  }),
}))

const SKILL = {
  id: "skill-1",
  workspace_id: "workspace-1",
  name: "Example skill",
  slug: "example-skill",
  description: null,
  current_version_id: "version-1",
  folder_id: null,
  tags: [{ id: "tag-1", name: "important", ref: "important", color: "#000" }],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <SkillsDashboard workspaceId="workspace-1" />
    </QueryClientProvider>
  )
}

function lastOptions(mock: jest.Mock): { enabled?: boolean } | undefined {
  const call = mock.mock.calls.at(-1)
  return call?.at(-1) as { enabled?: boolean } | undefined
}

describe("SkillsDashboard entitlement split", () => {
  beforeEach(() => {
    mockHasEntitlement.mockReset()
    mockHasEntitlement.mockReturnValue(false)
    mockSearchParams.current = new URLSearchParams()
    mockUseSkills.mockReset()
    mockUseSkills.mockReturnValue({
      skills: [SKILL],
      skillsLoading: false,
      skillsError: null,
    })
    mockUseSkillDirectoryItems.mockReset()
    mockUseSkillDirectoryItems.mockReturnValue({
      directoryItems: [],
      directoryItemsIsLoading: false,
      directoryItemsError: null,
    })
    mockUseSkillTagCatalog.mockReset()
    mockUseSkillTagCatalog.mockReturnValue({
      skillTags: [],
      skillTagsIsLoading: false,
    })
    mockUseAgentPresets.mockReset()
    mockUseAgentPresets.mockReturnValue({
      presets: [
        { id: "agent-1", name: "Triage", skill_ids: ["skill-1"] },
        { id: "agent-empty", name: "Empty", skill_ids: [] },
      ],
      presetsIsLoading: false,
      presetsError: null,
    })
  })

  it("renders a flat list and disables organization queries when unentitled", () => {
    mockSearchParams.current = new URLSearchParams("view=folders&path=/legacy/")

    renderDashboard()

    expect(lastOptions(mockUseSkills)?.enabled).toBe(true)
    expect(lastOptions(mockUseSkillDirectoryItems)?.enabled).toBe(false)
    expect(lastOptions(mockUseSkillTagCatalog)?.enabled).toBe(false)
    expect(screen.getByText("Example skill")).toBeInTheDocument()
    expect(screen.queryByText("important")).not.toBeInTheDocument()
    expect(screen.queryByText("Folders")).not.toBeInTheDocument()
  })

  it("uses the folder directory and skill tags when entitled", () => {
    mockHasEntitlement.mockImplementation((key) => key === "agent_addons")
    mockSearchParams.current = new URLSearchParams("view=folders")
    mockUseSkillDirectoryItems.mockReturnValue({
      directoryItems: [
        {
          id: "folder-1",
          name: "Shared",
          path: "/shared/",
          workspace_id: "workspace-1",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          type: "folder",
          num_items: 1,
        },
        {
          type: "skill",
          ...SKILL,
        },
      ],
      directoryItemsIsLoading: false,
      directoryItemsError: null,
    })
    mockUseSkillTagCatalog.mockReturnValue({
      skillTags: [
        { id: "tag-1", name: "important", ref: "important", color: "#000" },
      ],
      skillTagsIsLoading: false,
    })

    renderDashboard()

    expect(lastOptions(mockUseSkills)?.enabled).toBe(false)
    expect(lastOptions(mockUseSkillDirectoryItems)?.enabled).toBe(true)
    expect(lastOptions(mockUseSkillTagCatalog)?.enabled).toBe(true)
    expect(screen.getByText("Shared")).toBeInTheDocument()
    expect(screen.getByText("important")).toBeInTheDocument()
  })

  it("narrows the list to skills attached to the selected agents", () => {
    mockUseSkills.mockReturnValue({
      skills: [SKILL, { ...SKILL, id: "skill-2", name: "Unattached skill" }],
      skillsLoading: false,
      skillsError: null,
    })
    mockSearchParams.current = new URLSearchParams("agent=agent-1")

    renderDashboard()

    expect(screen.getByText("Example skill")).toBeInTheDocument()
    expect(screen.queryByText("Unattached skill")).not.toBeInTheDocument()
  })

  it("shows the filtered empty state when selected agents have no skills", () => {
    mockSearchParams.current = new URLSearchParams("agent=agent-empty")

    renderDashboard()

    expect(screen.queryByText("Example skill")).not.toBeInTheDocument()
    expect(screen.getByText("No skills match your filters")).toBeInTheDocument()
  })

  it("searches the flat catalog in folder view so nested skills match", () => {
    mockHasEntitlement.mockImplementation((key) => key === "agent_addons")
    mockSearchParams.current = new URLSearchParams(
      "view=folders&path=/&agent=agent-1"
    )
    mockUseSkills.mockReturnValue({
      skills: [{ ...SKILL, folder_id: "folder-1" }],
      skillsLoading: false,
      skillsError: null,
    })

    renderDashboard()

    expect(lastOptions(mockUseSkills)?.enabled).toBe(true)
    expect(lastOptions(mockUseSkillDirectoryItems)?.enabled).toBe(false)
    expect(screen.getByText("Example skill")).toBeInTheDocument()
  })
})
