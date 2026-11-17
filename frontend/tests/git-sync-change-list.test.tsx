/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactElement } from "react"
import {
  type CatalogMappingRequirement,
  workflowsListSyncDiffs,
} from "@/client"
import { GitSyncChangeList } from "@/components/workspace-sync/git-sync-change-list"
import { CatalogMappingRequirements } from "@/components/workspace-sync/mapping-requirements-card"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/components/workspace-sync/unified-diff", () => ({
  UnifiedDiff: ({ diff }: { diff: string }) => <pre>{diff}</pre>,
}))

const mockDiff = {
  title: null,
  resource_type: "variable" as const,
  source_id: "example",
  source_path: "variables/example.yml",
  change_type: "modified" as const,
  diff: "",
}

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  workflowsListSyncDiffs: jest.fn(() => Promise.resolve({ items: [mockDiff] })),
  workflowsGetSyncDiff: jest.fn(() =>
    Promise.resolve({ ...mockDiff, diff: "@@ -1 +1 @@\n-before\n+after" })
  ),
}))

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const skill = {
  resource_type: "skill",
  source_id: "triage",
  name: "Triage",
  path: "skills/triage/skill.yml",
}

describe("GitSyncChangeList", () => {
  it("shows the companion file path and counts content that looks like a header", async () => {
    const user = userEvent.setup()
    renderWithClient(
      <GitSyncChangeList
        workspaceId="workspace-1"
        direction="pull"
        summary="1 modified"
        resources={[skill]}
        diffs={[
          {
            resource_type: "skill",
            source_id: "triage",
            source_path: "skills/triage/notes.md",
            change_type: "modified",
            title: "Triage",
            diff: [
              "--- a/skills/triage/notes.md",
              "+++ b/skills/triage/notes.md",
              "@@ -1,2 +1,2 @@",
              "--- old rule",
              "+++ new rule",
            ].join("\n"),
          },
        ]}
        fileCount={1}
      />
    )

    await user.click(screen.getByRole("button", { name: /Skills/ }))
    await user.click(screen.getByRole("button", { name: /Triage/ }))

    // The row shows skill.yml, so the diff names the file it is for.
    expect(screen.getByText("skills/triage/notes.md")).toBeInTheDocument()
    expect(screen.getByText("+1")).toBeInTheDocument()
    expect(screen.getByText("−1")).toBeInTheDocument()
  })
})

describe("CatalogMappingRequirements", () => {
  const requirement: CatalogMappingRequirement = {
    source_catalog_id: "source",
    model_provider: "custom-model-provider",
    model_name: "shared-model",
    reason: "invalid_selection",
    message: "The selected target is no longer available.",
    candidates: [
      {
        catalog_id: "target-east",
        model_provider: "custom-model-provider",
        model_name: "shared-model",
        provider_name: "Provider East",
        model_display_name: null,
        endpoint_hostname: "east.models.example.com",
        origin: "custom_provider",
      },
    ],
    affected_presets: [],
    affected_workflows: [],
  }

  it("does not call a rejected target a match", () => {
    render(
      <CatalogMappingRequirements
        requirements={[requirement]}
        selections={{ source: "revoked-target" }}
        onChange={jest.fn()}
        disabled={false}
      />
    )

    expect(screen.getByText("Needs a match")).toBeInTheDocument()
    expect(
      screen.getByRole("combobox", {
        name: "Target model for shared-model (custom-model-provider)",
      })
    ).toBeInTheDocument()
  })

  it("calls a listed target a match", () => {
    render(
      <CatalogMappingRequirements
        requirements={[requirement]}
        selections={{ source: "target-east" }}
        onChange={jest.fn()}
        disabled={false}
      />
    )

    expect(screen.getByText("Matched")).toBeInTheDocument()
  })
})

it("lists a paginated operation preview's files in grouped rows", async () => {
  renderWithClient(
    <GitSyncChangeList
      workspaceId="workspace-1"
      operationId="synthetic-operation"
      direction="push"
      summary="Prepared changes"
      resources={[]}
      diffs={[]}
      fileCount={0}
      diffCount={3}
    />
  )
  expect(screen.getByText("3 files")).toBeInTheDocument()
  expect(screen.queryByText("0 files")).not.toBeInTheDocument()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: /Variables/ }))
  expect(workflowsListSyncDiffs).toHaveBeenCalledWith({
    workspaceId: "workspace-1",
    operationId: "synthetic-operation",
    cursor: undefined,
  })
  await user.click(
    screen.getByRole("button", { name: /variables\/example.yml/ })
  )
  expect(await screen.findByText(/\+after/)).toBeInTheDocument()
})
