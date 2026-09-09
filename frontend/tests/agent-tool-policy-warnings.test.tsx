import { render, screen } from "@testing-library/react"
import {
  type AgentToolPolicyPreviewState,
  AgentToolPolicyWarnings,
} from "@/components/agents/agent-tool-policy-warnings"

const preview: AgentToolPolicyPreviewState = {
  isPending: false,
  isError: false,
  data: {
    blocked_tools: [
      {
        tool_id: "tools.example.send_message",
        skill_id: "skill-test",
        skill_name: "Triage",
      },
    ],
  },
}

test("renders the server's blocked tools and skill provenance", () => {
  render(<AgentToolPolicyWarnings preview={preview} />)
  expect(screen.getByRole("alert")).toHaveTextContent("skill “Triage”")
  expect(screen.getByText("tools.example.send_message")).toBeInTheDocument()
  expect(screen.getByRole("alert")).toHaveTextContent("The agent can run")
})

test("clears warnings when the shared preview permits the tools", () => {
  const { rerender } = render(<AgentToolPolicyWarnings preview={preview} />)
  rerender(<AgentToolPolicyWarnings preview={{ ...preview, data: {} }} />)
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

test("identifies directly selected actions", () => {
  render(
    <AgentToolPolicyWarnings
      preview={{
        ...preview,
        data: { blocked_tools: [{ tool_id: "tools.example.delete_message" }] },
      }}
    />
  )
  expect(screen.getByRole("alert")).toHaveTextContent(
    "the preset's action list"
  )
})

test("explains skill-derived internet requirements even without blocked tools", () => {
  render(
    <AgentToolPolicyWarnings
      preview={{
        ...preview,
        data: {
          requires_internet_access: true,
          internet_sources: [
            {
              tool_id: "mcp.synthetic",
              skill_id: "skill-test",
              skill_name: "Triage",
            },
          ],
        },
      }}
    />
  )
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Skill tools require internet access"
  )
  expect(screen.getByRole("alert")).toHaveTextContent("mcp.synthetic")
  expect(screen.getByRole("alert")).toHaveTextContent("skill “Triage”")
})

test("leaves direct MCP internet requirements to the existing toggle", () => {
  render(
    <AgentToolPolicyWarnings
      preview={{
        ...preview,
        data: {
          requires_internet_access: true,
          internet_sources: [{ tool_id: "mcp.synthetic" }],
        },
      }}
    />
  )
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

test("shows preview failures instead of implying all tools are allowed", () => {
  render(<AgentToolPolicyWarnings preview={{ ...preview, isError: true }} />)
  expect(
    screen.getByText("Unable to check tool policy. Please try again.")
  ).toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

test("shows loading while unsaved selections are being evaluated", () => {
  render(
    <AgentToolPolicyWarnings preview={{ isError: false, isPending: true }} />
  )
  expect(screen.getByText("Checking tool policy…")).toBeInTheDocument()
})
