import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { PullResult } from "@/client"
import { WorkspaceSyncPullTab } from "@/components/workspace-sync/pull-tab"

const mockPullWorkflows = jest.fn()

jest.mock("@/hooks/use-workspace-sync", () => ({
  useWorkflowSync: () => ({
    pullWorkflows: mockPullWorkflows,
    pullWorkflowsIsPending: false,
  }),
}))

jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

const COMMIT_SHA = "a".repeat(40)

function previewResult(overrides: Partial<PullResult> = {}): PullResult {
  return {
    success: true,
    commit_sha: COMMIT_SHA,
    workflows_found: 0,
    workflows_imported: 0,
    diagnostics: [],
    message: "Preview ready",
    ...overrides,
  }
}

const unmatchedStore = {
  source_store: "source-production",
  reason: "unresolved" as const,
  message:
    "No store named 'source-production' is authorized for this workspace.",
  candidates: [
    {
      store_id: "store-target",
      name: "target-production",
      region: "us-east-1",
    },
  ],
  affected_secrets: [
    { secret_name: "vendor_api", environment: "default", path: "a" },
    { secret_name: "vendor_app", environment: "default", path: "b" },
  ],
}

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn()
  Element.prototype.hasPointerCapture = jest.fn(() => false)
  Element.prototype.releasePointerCapture = jest.fn()
})

beforeEach(() => {
  mockPullWorkflows.mockReset()
})

test("one store choice in the pull preview applies to every secret using that name", async () => {
  mockPullWorkflows
    .mockResolvedValueOnce(
      previewResult({ secret_store_mapping_requirements: [unmatchedStore] })
    )
    .mockResolvedValueOnce(previewResult())
    .mockResolvedValueOnce(previewResult({ message: "Pulled" }))
  const user = userEvent.setup()
  render(
    <WorkspaceSyncPullTab
      workspaceId="ws"
      provider="gitlab"
      commits={[
        {
          sha: COMMIT_SHA,
          message: "Export",
          author: "QA",
          author_email: "qa@example.com",
          date: "2026-10-06T00:00:00Z",
          tags: [],
        },
      ]}
      commitsIsLoading={false}
      commitsError={null}
    />
  )

  await user.click(screen.getByRole("button", { name: /Preview changes/ }))
  expect(await screen.findByText("Choose secret stores")).toBeInTheDocument()
  expect(screen.getByText("Affects vendor_api, vendor_app")).toBeInTheDocument()
  // Unlinked imports are allowed, so applying stays possible before a choice.
  expect(screen.getByRole("button", { name: /Apply pull/ })).toBeEnabled()

  await user.click(
    screen.getByRole("combobox", { name: "Target store for source-production" })
  )
  await user.click(
    await screen.findByRole("option", { name: "target-production (us-east-1)" })
  )
  expect(screen.getByRole("button", { name: /Apply pull/ })).toBeDisabled()

  await user.click(screen.getByRole("button", { name: /Preview changes/ }))
  const mapping = [
    { source_store: "source-production", target_store_id: "store-target" },
  ]
  expect(mockPullWorkflows).toHaveBeenLastCalledWith(
    expect.objectContaining({ dry_run: true, secret_store_mappings: mapping })
  )

  await user.click(await screen.findByRole("button", { name: /Apply pull/ }))
  expect(mockPullWorkflows).toHaveBeenLastCalledWith(
    expect.not.objectContaining({ dry_run: true })
  )
  expect(mockPullWorkflows).toHaveBeenLastCalledWith(
    expect.objectContaining({ secret_store_mappings: mapping })
  )
})

test("leaving a store unlinked sends no mapping for it", async () => {
  mockPullWorkflows
    .mockResolvedValueOnce(
      previewResult({ secret_store_mapping_requirements: [unmatchedStore] })
    )
    .mockResolvedValueOnce(
      previewResult({ secret_store_mapping_requirements: [unmatchedStore] })
    )
  const user = userEvent.setup()
  render(
    <WorkspaceSyncPullTab
      workspaceId="ws"
      provider="gitlab"
      commits={[
        {
          sha: COMMIT_SHA,
          message: "Export",
          author: "QA",
          author_email: "qa@example.com",
          date: "2026-10-06T00:00:00Z",
          tags: [],
        },
      ]}
      commitsIsLoading={false}
      commitsError={null}
    />
  )

  await user.click(screen.getByRole("button", { name: /Preview changes/ }))
  await user.click(
    await screen.findByRole("combobox", {
      name: "Target store for source-production",
    })
  )
  await user.click(
    await screen.findByRole("option", { name: "Leave unlinked" })
  )
  await user.click(screen.getByRole("button", { name: /Preview changes/ }))

  expect(mockPullWorkflows).toHaveBeenLastCalledWith(
    expect.objectContaining({ dry_run: true, secret_store_mappings: [] })
  )
})
