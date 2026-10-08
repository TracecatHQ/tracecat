import { fireEvent, render, screen } from "@testing-library/react"
import type React from "react"
import type { LibrarySkillDetailRead } from "@/client"
import { SkillLibrarySkillView } from "@/components/skills/skill-library-skill-view"

jest.mock("streamdown", () => ({
  Streamdown: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="markdown">{children}</div>
  ),
}))

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const mockUseLibrarySkill = jest.fn()

jest.mock("@/hooks/use-skill-library", () => ({
  useLibrarySkill: (...args: unknown[]) => mockUseLibrarySkill(...args),
  useSkillLibrary: () => ({}),
}))

const SKILL: LibrarySkillDetailRead = {
  slug: "security-alert-triage",
  description: "Triage Elastic Security alerts.",
  installed: false,
  source: {
    group: "Elastic",
    provider: null,
    kind: "upstream",
    repo: "elastic/agent-skills",
    commit: "a".repeat(40),
    license: "Apache-2.0",
    url: "https://github.com/elastic/agent-skills/tree/aaa/skills/security/alert-triage",
  },
  files: [
    {
      path: "SKILL.md",
      size_bytes: 80,
      content:
        "---\nname: security-alert-triage\ndescription: Triage.\n---\n# Alert Triage\n",
    },
    {
      path: "references/classification-guide.md",
      size_bytes: 12,
      content: "Guide body.",
    },
    { path: "logo.bin", size_bytes: 2, content: null },
  ],
}

describe("SkillLibrarySkillView", () => {
  beforeEach(() => {
    mockUseLibrarySkill.mockReturnValue({
      librarySkill: SKILL,
      librarySkillIsLoading: false,
      librarySkillError: null,
    })
  })

  it("shows the source, frontmatter, and instructions for SKILL.md", () => {
    render(<SkillLibrarySkillView slug="security-alert-triage" />)

    expect(mockUseLibrarySkill).toHaveBeenCalledWith(
      "workspace-1",
      "security-alert-triage"
    )
    expect(screen.getByText("Elastic")).toBeInTheDocument()
    expect(screen.queryByText(/maintained/i)).not.toBeInTheDocument()
    expect(screen.queryByText("Apache-2.0")).not.toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /elastic\/agent-skills/ })
    ).toHaveAttribute("href", SKILL.source?.url)
    expect(
      screen.getByText("Triage Elastic Security alerts.")
    ).toBeInTheDocument()
    expect(screen.getByTestId("markdown")).toHaveTextContent("# Alert Triage")
  })

  it("previews a locally maintained skill without a fabricated upstream link", () => {
    mockUseLibrarySkill.mockReturnValue({
      librarySkill: {
        ...SKILL,
        source: {
          group: "AWS",
          provider: "Tracecat",
          kind: "local",
          repo: null,
          commit: null,
          url: null,
          license: "AGPL-3.0-only",
        },
      },
      librarySkillIsLoading: false,
      librarySkillError: null,
    })
    render(<SkillLibrarySkillView slug="security-alert-triage" />)
    expect(screen.getByText("AWS")).toBeInTheDocument()
    expect(screen.getByText("Maintained by Tracecat")).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: /elastic\/agent-skills/ })
    ).not.toBeInTheDocument()
  })

  it("titles a standalone skill's source with its maintainer alone", () => {
    mockUseLibrarySkill.mockReturnValue({
      librarySkill: {
        ...SKILL,
        source: {
          group: null,
          provider: "Tracecat",
          kind: "local",
          repo: null,
          commit: null,
          url: null,
          license: "AGPL-3.0-only",
        },
      },
      librarySkillIsLoading: false,
      librarySkillError: null,
    })
    render(<SkillLibrarySkillView slug="security-alert-triage" />)
    expect(screen.getByText("Tracecat")).toBeInTheDocument()
    expect(screen.queryByText(/maintained by/i)).not.toBeInTheDocument()
  })

  it("shows other files as read-only text", () => {
    render(<SkillLibrarySkillView slug="security-alert-triage" />)

    fireEvent.click(screen.getByText("logo.bin"))
    expect(
      screen.getByText("Binary file; no preview available.")
    ).toBeInTheDocument()
  })

  it("reports a skill missing from the library", () => {
    mockUseLibrarySkill.mockReturnValue({
      librarySkill: undefined,
      librarySkillIsLoading: false,
      librarySkillError: null,
    })

    render(<SkillLibrarySkillView slug="missing" />)

    expect(
      screen.getByText("This skill is not in the skill library.")
    ).toBeInTheDocument()
  })
})
