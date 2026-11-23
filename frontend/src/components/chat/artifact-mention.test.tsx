import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { casesQueryCases } from "@/client"
import {
  ArtifactMentionLink,
  ArtifactPanelContext,
} from "@/components/chat/artifact-mention"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

jest.mock("@/client", () => ({
  casesQueryCases: jest.fn(),
}))
// Streamdown is ESM-only; the reply text itself is not under test here.
jest.mock("@/components/ai-elements/response", () => ({
  Response: ({ children }: { children: ReactNode }) => <p>{children}</p>,
}))

const CASE_ID = "11111111-1111-4111-8111-111111111111"
const CASE_HREF = `/tracecat-ref/v1/artifact/case/${CASE_ID}`

const queryCases = jest.mocked(casesQueryCases)

function forbidden() {
  return Object.assign(new Error("Forbidden"), { status: 403, body: {} })
}

function renderIn(ui: ReactNode, openArtifact?: jest.Mock) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const content = openArtifact ? (
    <ArtifactPanelContext.Provider value={openArtifact}>
      {ui}
    </ArtifactPanelContext.Provider>
  ) : (
    ui
  )
  const { container } = render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceIdProvider workspaceId="ws-1">{content}</WorkspaceIdProvider>
    </QueryClientProvider>
  )
  return { user: userEvent.setup(), container }
}

beforeEach(() => {
  jest.clearAllMocks()
  queryCases.mockResolvedValue({
    items: [
      {
        id: CASE_ID,
        short_id: "CASE-0142",
        summary: "Beacon on build host",
        status: "in_progress",
        severity: "high",
        priority: "medium",
      },
    ],
  } as unknown as Awaited<ReturnType<typeof casesQueryCases>>)
})

it("draws a chip with the case's live status and severity", async () => {
  renderIn(
    <ArtifactMentionLink href={CASE_HREF}>CASE-0142</ArtifactMentionLink>
  )

  expect(await screen.findByText("In Progress")).toBeInTheDocument()
  expect(screen.getByText("High")).toBeInTheDocument()
  // Search records no case view, unlike the case detail route.
  expect(queryCases).toHaveBeenCalledWith({
    workspaceId: "ws-1",
    requestBody: { case_id: CASE_ID, limit: 1, include_count: false },
  })
})

it("opens the case's panel tab on a surface with an artifact panel", async () => {
  const openArtifact = jest.fn()
  const { user } = renderIn(
    <ArtifactMentionLink href={CASE_HREF}>CASE-0142</ArtifactMentionLink>,
    openArtifact
  )

  await user.click(await screen.findByRole("button", { name: /CASE-0142/ }))

  expect(openArtifact).toHaveBeenCalledWith({
    type: "case",
    id: CASE_ID,
    title: "Beacon on build host",
    severity: "high",
    status: "in_progress",
  })
})

it("links to the case page on a surface without an artifact panel", async () => {
  renderIn(
    <ArtifactMentionLink href={CASE_HREF}>CASE-0142</ArtifactMentionLink>
  )

  expect(
    await screen.findByRole("link", { name: /CASE-0142/ })
  ).toHaveAttribute("href", `/workspaces/ws-1/cases/${CASE_ID}`)
})

it.each([
  ["without access", () => queryCases.mockRejectedValue(forbidden())],
  [
    "for an unknown case",
    () =>
      queryCases.mockResolvedValue({ items: [] } as unknown as Awaited<
        ReturnType<typeof casesQueryCases>
      >),
  ],
])("shows the label only %s", async (_name, arrange) => {
  arrange()
  const { container } = renderIn(
    <ArtifactMentionLink href={CASE_HREF}>CASE-0142</ArtifactMentionLink>,
    jest.fn()
  )

  await waitFor(() => expect(queryCases).toHaveBeenCalled())
  expect(screen.queryByRole("button")).not.toBeInTheDocument()
  expect(screen.queryByRole("link")).not.toBeInTheDocument()
  expect(container.textContent).toBe("CASE-0142")
})

it("keeps ordinary links as new-tab links", () => {
  renderIn(
    <ArtifactMentionLink href="https://example.com/docs">
      docs
    </ArtifactMentionLink>
  )

  const link = screen.getByRole("link", { name: "docs" })
  expect(link).toHaveAttribute("target", "_blank")
  expect(link).toHaveAttribute("href", "https://example.com/docs")
})
