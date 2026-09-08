import { render, screen } from "@testing-library/react"
import { AgentNamespacePolicyWarnings } from "@/components/agents/agent-namespace-policy-warnings"

const mockUseSkills = jest.fn()
const mockUseSkillVersion = jest.fn()
jest.mock("@/hooks/use-skills", () => ({
  useSkills: (...args: unknown[]) => mockUseSkills(...args),
  useSkillVersion: (...args: unknown[]) => mockUseSkillVersion(...args),
}))

const props = {
  workspaceId: "workspace-test",
  namespaces: ["core.cases"],
  skills: [{ skillId: "skill-test" }],
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUseSkills.mockReturnValue({
    skills: [
      { id: "skill-test", name: "Triage", current_version_id: "version-2" },
    ],
    skillsLoading: false,
    skillsError: null,
  })
  mockUseSkillVersion.mockReturnValue({
    version: {
      name: "Triage",
      registry_tool_ids: ["core.cases.get_case", "tools.example.send_message"],
    },
    versionLoading: false,
    versionError: null,
  })
})

test("names the policy, skill, and only the blocked published tools", () => {
  render(<AgentNamespacePolicyWarnings {...props} />)
  expect(screen.getByRole("alert")).toHaveTextContent(
    "The namespace policy (core.cases) blocks these tools from skill “Triage”"
  )
  expect(screen.getByText("tools.example.send_message")).toBeInTheDocument()
  expect(screen.queryByText("core.cases.get_case")).not.toBeInTheDocument()
  expect(mockUseSkillVersion).toHaveBeenCalledWith(
    "workspace-test",
    "skill-test",
    "version-2"
  )
})

test("clears warnings immediately when the policy permits the tools", () => {
  const { rerender } = render(<AgentNamespacePolicyWarnings {...props} />)
  rerender(
    <AgentNamespacePolicyWarnings {...props} namespaces={["core", "tools"]} />
  )
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

test("does not fetch skill data without a namespace policy", () => {
  render(<AgentNamespacePolicyWarnings {...props} namespaces={[]} />)
  expect(mockUseSkills).not.toHaveBeenCalled()
  expect(mockUseSkillVersion).not.toHaveBeenCalled()
})

test("also identifies blocked directly selected actions", () => {
  render(
    <AgentNamespacePolicyWarnings
      {...props}
      skills={[]}
      actions={["tools.example.delete_message"]}
    />
  )
  expect(screen.getByRole("alert")).toHaveTextContent(
    "the preset's action list"
  )
  expect(screen.getByText("tools.example.delete_message")).toBeInTheDocument()
})

test("reports failed grant loading instead of implying all tools are allowed", () => {
  mockUseSkillVersion.mockReturnValue({
    versionError: new Error("Unavailable"),
  })
  render(<AgentNamespacePolicyWarnings {...props} />)
  expect(
    screen.getByText("Unable to check tools from skill “Triage”.")
  ).toBeInTheDocument()
})
