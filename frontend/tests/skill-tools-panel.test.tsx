import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import type {
  MCPIntegrationRead,
  RegistryActionReadMinimal,
  SkillDraftRead,
  SkillRead,
} from "@/client"
import { EditorPanel } from "@/components/skills/editor-panel"
import { SkillToolsPanel } from "@/components/skills/skill-tools-panel"
import { useSkillsStudio } from "@/components/skills/use-skills-studio"
import { splitMarkdownFrontmatter } from "@/lib/markdown-frontmatter"
import { readSkillFrontmatterTools } from "@/lib/skill-tools"
import { mcpIntegration, registryTool } from "./fixtures/agent-preset-tools"

const getCase = registryTool("core.cases.get_case", {
  default_title: "Get case",
  display_group: "Cases",
})
const listCases = registryTool("core.cases.list_cases", {
  default_title: "List cases",
  display_group: "Cases",
})
const httpIntegration = mcpIntegration({
  name: "Synthetic",
  slug: "synthetic",
  tools: [
    { name: "read", enabled: true, status: "available" },
    { name: "write", enabled: true, status: "available" },
  ],
})

let mockRegistryActions: RegistryActionReadMinimal[] = []
let mockMcpIntegrations: MCPIntegrationRead[] = []
let mockLoading = false
let mockError: Error | null = null

jest.mock("@/lib/hooks", () => ({
  ...jest.requireActual("@/lib/hooks"),
  useRegistryActions: () => ({
    registryActions: mockLoading || mockError ? undefined : mockRegistryActions,
    registryActionsIsLoading: mockLoading,
    registryActionsError: mockError,
  }),
  useListMcpIntegrations: () => ({
    mcpIntegrations: mockLoading || mockError ? undefined : mockMcpIntegrations,
    mcpIntegrationsIsLoading: mockLoading,
    mcpIntegrationsError: mockError,
  }),
}))

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => 440,
  })
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => 1000,
  })
  HTMLElement.prototype.scrollTo = function scrollTo(
    options?: ScrollToOptions | number
  ) {
    if (typeof options !== "number") this.scrollTop = options?.top ?? 0
    this.dispatchEvent(new Event("scroll"))
  }
})

beforeEach(() => {
  mockRegistryActions = [getCase, listCases]
  mockMcpIntegrations = [httpIntegration]
  mockLoading = false
  mockError = null
})

const onChange = jest.fn()
let currentFrontmatter = ""

/** Keeps the frontmatter in state, as the editor's draft does. */
function Panel({ initial }: { initial: string }) {
  const [frontmatter, setFrontmatter] = useState(initial)
  currentFrontmatter = frontmatter
  return (
    <SkillToolsPanel
      workspaceId="workspace-test"
      frontmatter={frontmatter}
      onChange={(next: string) => {
        onChange(next)
        setFrontmatter(next)
      }}
    />
  )
}

function tools(): string[] {
  return readSkillFrontmatterTools(currentFrontmatter).tools
}

/** The count beside the list's "Tools" heading, or null when none is shown. */
function headerCount(): string | null {
  const heading = screen.getByRole("heading", { name: "Tools" })
  return heading.nextElementSibling?.tagName === "SPAN"
    ? heading.nextElementSibling.textContent
    : null
}

describe("SkillToolsPanel", () => {
  beforeEach(() => onChange.mockClear())

  it("renders groups and counts from frontmatter", async () => {
    const user = userEvent.setup()
    render(
      <Panel
        initial={`name: triage
metadata:
  tools:
    - core.cases.get_case
    - mcp.synthetic
    - mcp.synthetic.read`}
      />
    )
    expect(headerCount()).toBe("3")
    const cases = screen.getByRole("button", { name: /Cases core\.cases/ })
    expect(within(cases).getByText("1 of 2")).toBeInTheDocument()
    const synthetic = screen.getByRole("button", {
      name: /Synthetic synthetic/,
    })
    expect(within(synthetic).getByText("2 of 3")).toBeInTheDocument()
    await user.click(synthetic)
    expect(screen.getByText("All tools")).toBeInTheDocument()
    expect(screen.getByTitle("mcp.synthetic.read")).toBeInTheDocument()
    expect(screen.queryByText("Auto")).not.toBeInTheDocument()
    expect(screen.queryByText("Unavailable")).not.toBeInTheDocument()
  })

  it("adds and removes a tool, leaving the rest of the frontmatter byte-identical", async () => {
    const user = userEvent.setup()
    const before =
      "# keep\nname: triage # inline\nmetadata:\n  owner: team\n  tools: "
    const after = "\nlicense: 0123\n"
    render(<Panel initial={`${before}["core.cases.get_case"]${after}`} />)
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(await screen.findByRole("option", { name: /List cases/ }))
    await user.click(screen.getByRole("button", { name: "Done" }))
    expect(currentFrontmatter).toBe(
      `${before}["core.cases.get_case","core.cases.list_cases"]${after}`
    )
    await user.click(screen.getByRole("button", { name: /Cases core\.cases/ }))
    await user.click(
      screen.getByRole("button", { name: "Remove core.cases.get_case" })
    )
    expect(currentFrontmatter).toBe(
      `${before}["core.cases.list_cases"]${after}`
    )
  })

  it("replaces a server's per-tool grants with the whole-server grant", async () => {
    const user = userEvent.setup()
    render(<Panel initial="name: triage" />)
    expect(screen.getByText("No tools selected.")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(
      await screen.findByRole("option", { name: /mcp\.synthetic\.read/ })
    )
    await user.click(screen.getByRole("button", { name: "Done" }))
    expect(tools()).toEqual(["mcp.synthetic.read"])

    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(await screen.findByRole("option", { name: /All tools/ }))
    await user.click(screen.getByRole("button", { name: "Done" }))
    expect(tools()).toEqual(["mcp.synthetic"])

    // The whole-server grant wins over a tool picked afterwards.
    onChange.mockClear()
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(
      await screen.findByRole("option", { name: /mcp\.synthetic\.write/ })
    )
    await user.click(screen.getByRole("button", { name: "Done" }))
    expect(tools()).toEqual(["mcp.synthetic"])
    expect(onChange).not.toHaveBeenCalled()
  })

  it("stores only the whole-server grant when a server's group is selected", async () => {
    const user = userEvent.setup()
    render(<Panel initial="name: triage" />)
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(await screen.findByRole("button", { name: /^Synthetic/ }))
    await user.click(
      screen.getByRole("checkbox", { name: "Select all in Synthetic" })
    )
    await user.click(screen.getByRole("button", { name: "Done" }))
    expect(tools()).toEqual(["mcp.synthetic"])
  })

  it("loads stored whole-server and per-tool grants together until that server is edited", async () => {
    const user = userEvent.setup()
    render(
      <Panel initial="metadata: {tools: [mcp.synthetic.read, mcp.synthetic, core.cases.get_case]}" />
    )
    expect(screen.queryByText(/tool IDs/)).not.toBeInTheDocument()
    expect(headerCount()).toBe("3")
    expect(screen.getByRole("button", { name: "Add tools" })).toBeEnabled()
    // An edit elsewhere leaves the server's grants as written.
    await user.click(screen.getByRole("button", { name: /Cases core\.cases/ }))
    await user.click(
      screen.getByRole("button", { name: "Remove core.cases.get_case" })
    )
    expect(tools()).toEqual(["mcp.synthetic.read", "mcp.synthetic"])
    // Editing the server normalises it.
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(
      await screen.findByRole("option", { name: /mcp\.synthetic\.write/ })
    )
    await user.click(screen.getByRole("button", { name: "Done" }))
    expect(tools()).toEqual(["mcp.synthetic"])
  })

  it("opens the upgrade dialog for a locked registry action", async () => {
    mockRegistryActions = [
      getCase,
      registryTool("core.cases.locked_case", {
        default_title: "Locked case",
        availability: { locked: true },
      }),
    ]
    const user = userEvent.setup()
    render(<Panel initial="name: triage" />)
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(await screen.findByRole("option", { name: /Locked case/ }))
    expect(
      await screen.findAllByText("Upgrade to unlock this feature")
    ).not.toHaveLength(0)
    expect(onChange).not.toHaveBeenCalled()
  })

  it("returns focus to Add tools when the picker closes", async () => {
    const user = userEvent.setup()
    render(<Panel initial="name: triage" />)
    const add = screen.getByRole("button", { name: "Add tools" })
    for (const close of ["Cancel", "Done", "Escape"]) {
      await user.click(add)
      expect(await screen.findByRole("dialog")).toBeInTheDocument()
      if (close === "Escape") await user.keyboard("{Escape}")
      else await user.click(screen.getByRole("button", { name: close }))
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      await waitFor(() => expect(add).toHaveFocus())
      add.blur()
    }
  })

  it("disables Done over the 64-tool limit", async () => {
    mockRegistryActions = Array.from({ length: 65 }, (_, i) =>
      registryTool(`tools.vendor.action_${i}`)
    )
    const declared = mockRegistryActions
      .slice(0, 64)
      .map((action) => action.action)
    const user = userEvent.setup()
    render(
      <Panel initial={`metadata: { tools: ${JSON.stringify(declared)} }`} />
    )
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    expect(screen.getByText("64 of 64 tools")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled()
    await user.click(screen.getByRole("button", { name: /^Tools vendor/ }))
    await user.click(
      screen.getByRole("checkbox", { name: "Select all in Tools vendor" })
    )
    expect(
      screen.getByText("65 of 64 tools · remove 1 to continue")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "Done" }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it("offers no edits when metadata.tools is malformed", () => {
    render(
      <Panel
        initial={`name: triage
metadata:
  tools: core.cases.get_case`}
      />
    )
    expect(
      screen.getByText("metadata.tools must be a YAML list.")
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Add tools" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /^Remove/ })
    ).not.toBeInTheDocument()
    // Unreadable tools have no count, rather than a misleading zero.
    expect(headerCount()).toBeNull()
    expect(screen.queryByText("0")).not.toBeInTheDocument()
  })

  it("shows malformed IDs as unavailable and lets users remove them", async () => {
    const user = userEvent.setup()
    render(
      <Panel initial='metadata: { tools: ["not-a-tool", "core.cases.get_case"] }' />
    )
    expect(screen.getByText(/Invalid tool IDs: not-a-tool/)).toBeInTheDocument()
    expect(screen.getByText("Unavailable")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "Remove not-a-tool" }))
    expect(readSkillFrontmatterTools(currentFrontmatter)).toEqual({
      valid: true,
      tools: ["core.cases.get_case"],
    })
    expect(screen.queryByText(/Invalid tool IDs/)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeEnabled()
  })

  it("warns about individual stdio grants and allows their removal", async () => {
    mockMcpIntegrations = [
      mcpIntegration({
        name: "Synthetic",
        slug: "synthetic",
        server_type: "stdio",
        server_uri: null,
        stdio_command: "synthetic",
      }),
    ]
    const user = userEvent.setup()
    render(
      <Panel initial="metadata: {tools: [mcp.synthetic.read, mcp.synthetic]}" />
    )
    expect(
      screen.getByText(/Invalid tool IDs: mcp.synthetic.read/)
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
    await user.click(
      screen.getByRole("button", { name: "Remove mcp.synthetic.read" })
    )
    expect(
      readSkillFrontmatterTools(currentFrontmatter, mockMcpIntegrations)
    ).toEqual({ valid: true, tools: ["mcp.synthetic"] })
  })

  it("lists an unknown ID as unavailable and blocks additions until it is removed", async () => {
    const user = userEvent.setup()
    render(
      <Panel initial="metadata: {tools: [core.cases.removed, core.cases.get_case]}" />
    )
    expect(
      screen.getByText(/Unavailable tool IDs: core.cases.removed/)
    ).toBeInTheDocument()
    const heading = screen.getByRole("heading", { name: "Unavailable" })
    expect(
      within(heading.parentElement as HTMLElement).getByTitle(
        "core.cases.removed"
      )
    ).toBeInTheDocument()
    // Only tools in the catalogue count as selected; the ID is still stored.
    expect(headerCount()).toBe("1")
    expect(tools()).toEqual(["core.cases.removed", "core.cases.get_case"])
    expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
    await user.click(
      screen.getByRole("button", { name: "Remove core.cases.removed" })
    )
    expect(tools()).toEqual(["core.cases.get_case"])
    expect(screen.queryByText("Unavailable")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeEnabled()
  })

  it("reports a declared action that agents cannot call as unavailable", async () => {
    mockRegistryActions = [getCase, registryTool("core.script.run_python")]
    const user = userEvent.setup()
    render(
      <Panel initial="metadata: {tools: [core.script.run_python, core.cases.get_case]}" />
    )
    expect(
      screen.getByText(/Unavailable tool IDs: core.script.run_python/)
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
    await user.click(
      screen.getByRole("button", { name: "Remove core.script.run_python" })
    )
    expect(tools()).toEqual(["core.cases.get_case"])
    expect(screen.getByRole("button", { name: "Add tools" })).toBeEnabled()
  })

  it("shows no count and no fallback list while the catalogue loads", () => {
    mockLoading = true
    render(
      <Panel initial="metadata: {tools: [core.cases.removed, mcp.deleted]}" />
    )
    expect(screen.getByText("Loading tools...")).toBeInTheDocument()
    expect(headerCount()).toBeNull()
    expect(screen.queryByText(/Unavailable/)).not.toBeInTheDocument()
    expect(
      screen.queryByText("Existing tool IDs are preserved.")
    ).not.toBeInTheDocument()
    expect(screen.queryByTitle("core.cases.removed")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
  })

  it("preserves IDs without declaring them unavailable on a load error", async () => {
    mockError = new Error("Catalogue unavailable")
    const user = userEvent.setup()
    render(
      <Panel initial="metadata: {tools: [core.cases.removed, mcp.deleted]}" />
    )
    expect(screen.getByText("Tools could not be loaded.")).toBeInTheDocument()
    expect(headerCount()).toBeNull()
    expect(screen.queryByText(/Unavailable/)).not.toBeInTheDocument()
    expect(
      screen.getByText("Existing tool IDs are preserved.")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
    expect(screen.getByTitle("core.cases.removed")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Remove mcp.deleted" }))
    expect(tools()).toEqual(["core.cases.removed"])
  })

  it.each(["loading", "error"])(
    "closes an open picker when the catalogue enters %s",
    async (state) => {
      const user = userEvent.setup()
      const { rerender } = render(<Panel initial="name: triage" />)
      await user.click(screen.getByRole("button", { name: "Add tools" }))
      expect(await screen.findByRole("dialog")).toBeInTheDocument()
      mockLoading = state === "loading"
      mockError = state === "error" ? new Error("Catalogue unavailable") : null
      rerender(<Panel initial="name: triage" />)
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      expect(onChange).not.toHaveBeenCalled()
      // It stays closed once the catalogue is back.
      mockLoading = false
      mockError = null
      rerender(<Panel initial="name: triage" />)
      expect(screen.getByRole("button", { name: "Add tools" })).toBeEnabled()
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    }
  )
})

const mockSkill: SkillRead = {
  id: "skill-1",
  workspace_id: "workspace-test",
  name: "skill-1",
  slug: "skill-1",
  description: null,
  current_version_id: null,
  draft_revision: 1,
  created_at: "2026-04-10T00:00:00.000Z",
  updated_at: "2026-04-10T00:00:00.000Z",
  deleted_at: null,
  current_version: null,
  is_draft_publishable: true,
  draft_validation_errors: [],
  draft_file_count: 2,
}
const mockDraft: SkillDraftRead = {
  skill_id: "skill-1",
  skill_name: "skill-1",
  draft_revision: 1,
  name: "Skill 1",
  description: null,
  files: ["SKILL.md", "README.md"].map((path) => ({
    path,
    blob_id: `blob-${path}`,
    sha256: `sha-${path}`,
    size_bytes: 12,
    content_type: "text/markdown; charset=utf-8",
  })),
  is_publishable: true,
  validation_errors: [],
}
const SKILL_MD =
  "---\nname: triage\ndescription: Triage alerts.\n---\n\n# Triage\n"
let mockSkillMd = SKILL_MD

jest.mock(
  "next/dynamic",
  () => () =>
    function DynamicStub() {
      return null
    }
)
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useSearchParams: () => {
    const { startTransition, useEffect, useState } =
      jest.requireActual<typeof import("react")>("react")
    const [search, setSearch] = useState(window.location.search)
    useEffect(() => {
      const notify = () =>
        startTransition(() => setSearch(window.location.search))
      window.addEventListener("popstate", notify)
      return () => window.removeEventListener("popstate", notify)
    }, [])
    return new URLSearchParams(search)
  },
}))
jest.mock("@/hooks/use-skills", () => ({
  useSkill: () => ({ skill: mockSkill, skillLoading: false }),
  useSkillDraft: () => ({ draft: mockDraft, draftLoading: false }),
  useSkillVersions: () => ({ versions: [], versionsLoading: false }),
  useSkillDraftFile: (
    _workspaceId: string,
    _skillId: string,
    path: string | null
  ) => ({
    draftFile:
      path === null
        ? undefined
        : {
            kind: "inline",
            path,
            content_type: "text/markdown; charset=utf-8",
            size_bytes: mockSkillMd.length,
            text_content: path === "SKILL.md" ? mockSkillMd : "# Readme\n",
          },
    draftFileLoading: false,
  }),
  usePatchSkillDraft: () => ({ patchSkillDraft: jest.fn() }),
  useCreateSkillDraftUpload: () => ({ createSkillDraftUpload: jest.fn() }),
  usePublishSkill: () => ({ publishSkill: jest.fn() }),
  useRestoreSkillVersion: () => ({ restoreSkillVersion: jest.fn() }),
  useDeleteSkill: () => ({ deleteSkill: jest.fn() }),
}))

let studio: ReturnType<typeof useSkillsStudio>

function Studio() {
  studio = useSkillsStudio({
    workspaceId: "workspace-test",
    skillId: "skill-1",
  })
  return <EditorPanel {...studio} />
}

describe("skill editor split", () => {
  beforeEach(() => {
    jest.restoreAllMocks()
    mockSkillMd = SKILL_MD
    window.history.replaceState(null, "", "/skills/skill-1")
    const replaceState = window.history.replaceState.bind(window.history)
    jest.spyOn(window.history, "replaceState").mockImplementation((...args) => {
      replaceState(...args)
      // Next's history integration notifies useSearchParams on URL changes.
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
  })

  it("shows the tools panel beside the frontmatter and instructions of SKILL.md", () => {
    // A leftover view param from the earlier tab design changes nothing.
    window.history.replaceState(null, "", "/skills/skill-1?view=tools")
    render(<Studio />)
    expect(screen.queryByRole("tab")).not.toBeInTheDocument()
    expect(screen.getByText("Frontmatter")).toBeInTheDocument()
    expect(screen.getByText("Instructions")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Tools" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeEnabled()
    // The file tree divider plus the editor and tools divider.
    expect(screen.getAllByRole("separator")).toHaveLength(2)
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument()
    expect(new URLSearchParams(window.location.search).get("view")).toBe(
      "tools"
    )
    // Loading the skill stages nothing.
    expect(studio.hasUnsavedChanges).toBe(false)
    expect(studio.selectedFile?.change).toBeNull()
  })

  it("keeps the full-width editor for other files", () => {
    window.history.replaceState(null, "", "/skills/skill-1?file=README.md")
    render(<Studio />)
    expect(studio.selectedPath).toBe("README.md")
    expect(
      screen.queryByRole("heading", { name: "Tools" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Add tools" })
    ).not.toBeInTheDocument()
    expect(screen.getAllByRole("separator")).toHaveLength(1)
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument()
  })

  it("keeps the full-width editor for a SKILL.md without frontmatter", () => {
    mockSkillMd = "# Triage\n"
    render(<Studio />)
    expect(studio.selectedPath).toBe("SKILL.md")
    expect(
      screen.queryByRole("heading", { name: "Tools" })
    ).not.toBeInTheDocument()
    expect(screen.getAllByRole("separator")).toHaveLength(1)
  })

  it("stages a SKILL.md draft change for a tool picked before the editor is touched", async () => {
    const user = userEvent.setup()
    render(<Studio />)
    expect(studio.hasUnsavedChanges).toBe(false)
    await user.click(screen.getByRole("button", { name: "Add tools" }))
    await user.click(await screen.findByRole("option", { name: /Get case/ }))
    await user.click(screen.getByRole("button", { name: "Done" }))

    expect(studio.hasUnsavedChanges).toBe(true)
    expect(studio.selectedFile?.change).toMatchObject({ kind: "text" })
    const draft = splitMarkdownFrontmatter(studio.currentTextValue ?? "")
    expect(readSkillFrontmatterTools(draft?.frontmatter ?? "").tools).toEqual([
      "core.cases.get_case",
    ])
    expect(draft?.body).toBe(splitMarkdownFrontmatter(SKILL_MD)?.body)
    expect(headerCount()).toBe("1")
    // The tools write does not leave the markdown editor guard open.
    expect(studio.markdownEditorActivatedRef.current).toBe(false)

    // Reset reverts the tool change.
    await user.click(screen.getByRole("button", { name: "Reset" }))
    expect(studio.hasUnsavedChanges).toBe(false)
    expect(headerCount()).toBe("0")
    await act(async () => {})
  })
})
